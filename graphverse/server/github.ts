import { createHash } from 'node:crypto';
import { z } from 'zod';
import type { Edge, Entity, EntityType } from '../shared/types';
import { HttpError } from './repository';

export const repositoryName = z
  .string()
  .regex(/^[A-Za-z0-9][A-Za-z0-9-]{0,38}\/[A-Za-z0-9_.-]{1,100}$/);
export const githubId = (value: string) =>
  'gh_' + createHash('sha256').update(value).digest('hex').slice(0, 32);
export interface Inventory {
  repository: string;
  head: string;
  observed_at: string;
  fingerprint: string;
  nodes: Entity[];
  edges: Edge[];
  coverage: string[];
}
const repoSchema = z.object({
  id: z.number().int().positive(),
  full_name: repositoryName,
  private: z.boolean(),
  default_branch: z.string().min(1).max(255),
  owner: z.object({ login: z.string().max(100) }),
});
const branchSchema = z.object({
  protected: z.boolean(),
  commit: z.object({ sha: z.string().regex(/^[a-f0-9]{40}$/) }),
});
const treeSchema = z.object({
  truncated: z.boolean(),
  tree: z
    .array(z.object({ path: z.string().max(1000), type: z.string(), sha: z.string() }))
    .max(30000),
});

// Fixed HTTPS origin, GET only, no redirects, bounded streamed bodies, bounded retries.
// The optional token is used only on api.github.com, never stored in graph data.
export class GitHubReader {
  constructor(
    private readonly token = '',
    private readonly transport: typeof fetch = fetch,
  ) {}
  private async get(path: string, signal: AbortSignal): Promise<unknown> {
    for (let attempt = 0; attempt < 2; attempt++) {
      let response: Response;
      try {
        response = await this.transport('https://api.github.com' + path, {
          method: 'GET',
          redirect: 'error',
          signal: AbortSignal.any([signal, AbortSignal.timeout(8000)]),
          headers: {
            Accept: 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2026-03-10',
            'User-Agent': 'ShadowChainAI-readonly',
            ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}),
          },
        });
      } catch {
        if (attempt === 0 && !signal.aborted) continue;
        throw new HttpError(
          502,
          'github_unavailable',
          'GitHub could not be reached. Retry the synchronization.',
        );
      }
      if ([403, 429].includes(response.status)) {
        await response.body?.cancel();
        throw new HttpError(
          429,
          'github_rate_limit',
          'GitHub denied or rate-limited this read. Retry later or configure a server-side read token.',
        );
      }
      if (response.status === 404) {
        await response.body?.cancel();
        throw new HttpError(404, 'github_not_found', 'Public repository or revision not found.');
      }
      if (!response.ok) {
        await response.body?.cancel();
        if (response.status >= 500 && attempt === 0) continue;
        throw new HttpError(
          502,
          'github_response',
          'GitHub did not return a successful read response.',
        );
      }
      const reader = response.body?.getReader();
      if (!reader)
        throw new HttpError(502, 'github_response', 'GitHub returned an empty response.');
      const chunks: Uint8Array[] = [];
      let size = 0;
      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          size += value.length;
          if (size > 5_000_000) {
            await reader.cancel();
            throw new HttpError(
              413,
              'github_limit',
              'Repository metadata exceeds the supported read limit.',
            );
          }
          chunks.push(value);
        }
        return JSON.parse(Buffer.concat(chunks).toString('utf8'));
      } finally {
        reader.releaseLock();
      }
    }
    throw new HttpError(502, 'github_unavailable', 'GitHub could not be reached.');
  }
  async inventory(input: string): Promise<Inventory> {
    const name = repositoryName.parse(input).toLowerCase(),
      signal = AbortSignal.timeout(60000);
    const base = '/repos/' + name;
    const repo = repoSchema.parse(await this.get(base, signal));
    if (repo.private)
      throw new HttpError(
        403,
        'private_repository',
        'Private repositories cannot be imported into the public explorer.',
      );
    if (repo.full_name.toLowerCase() !== name)
      throw new HttpError(
        409,
        'repository_moved',
        'The repository changed identity. Review the configured source.',
      );
    const branch = branchSchema.parse(
      await this.get(base + '/branches/' + encodeURIComponent(repo.default_branch), signal),
    );
    const [rawTree, rawContributors] = await Promise.all([
      this.get(base + '/git/trees/' + branch.commit.sha + '?recursive=1', signal),
      this.get(base + '/contributors?per_page=10&page=1', signal),
    ]);
    const tree = treeSchema.parse(rawTree);
    const contributors = z
      .array(
        z.object({
          login: z
            .string()
            .regex(/^[A-Za-z0-9-]+(?:\[bot\])?$/)
            .max(100),
        }),
      )
      .max(10)
      .parse(rawContributors);
    const observed_at = new Date().toISOString(),
      nodes: Entity[] = [],
      edges: Edge[] = [];
    const provenance = `github:${name}@${branch.commit.sha}`;
    const node = (
      key: string,
      label: string,
      type: EntityType,
      cluster: string,
      description: string,
      severity = 'none',
      extra: Record<string, unknown> = {},
    ) => {
      const id = githubId(name + ':' + key);
      nodes.push({
        id,
        label: label.slice(0, 160),
        type,
        cluster,
        description,
        severity,
        source: provenance,
        created_at: observed_at,
        synthetic: false,
        ...extra,
      });
      return id;
    };
    const edge = (source: string, target: string, type: string, evidence: string) =>
      edges.push({
        id: githubId(name + ':' + source + ':' + type + ':' + target),
        source,
        target,
        type,
        evidence,
        confidence: 1,
        valid_from: observed_at,
        valid_to: null,
      });
    const root = node(
      'repository',
      repo.full_name,
      'Resource',
      'infrastructure',
      `Public GitHub repository observed at commit ${branch.commit.sha}. Public metadata is not proof of runtime access.`,
      'none',
      { source_url: `https://github.com/${name}`, head: branch.commit.sha },
    );
    const owner = node(
      'account:' + repo.owner.login.toLowerCase(),
      repo.owner.login,
      'Identity',
      'identity',
      'Public repository owner returned by GitHub. This is not a list of people with effective administrative access.',
    );
    edge(owner, root, 'OWNS', `GET ${base}: owner.login; observed ${observed_at}.`);
    for (const c of contributors) {
      const id = node(
        'account:' + c.login.toLowerCase(),
        c.login,
        'Identity',
        'identity',
        'Contributor listed by GitHub. Contribution does not establish current collaborator permissions.',
      );
      edge(
        id,
        root,
        'CONTRIBUTED_TO',
        `GET ${base}/contributors?per_page=10&page=1; bounded public contributor list.`,
      );
    }
    const branchNode = node(
      'branch',
      repo.default_branch,
      'Resource',
      'infrastructure',
      `Default branch observed at ${branch.commit.sha}.`,
    );
    edge(
      root,
      branchNode,
      'DEFAULT_BRANCH',
      `GET ${base}/branches/${encodeURIComponent(repo.default_branch)}; commit ${branch.commit.sha}.`,
    );
    const branchEvidence = node(
      'branch-evidence:' + branch.commit.sha + ':' + String(branch.protected),
      'Default branch observation',
      'Document',
      'incident',
      `GitHub legacy branch endpoint reported protected=${branch.protected}. Repository rulesets and organization policies are not evaluated.`,
      'none',
      { protected: branch.protected },
    );
    edge(
      branchNode,
      branchEvidence,
      'SUPPORTED_BY',
      `GET ${base}/branches/${encodeURIComponent(repo.default_branch)} at ${observed_at}.`,
    );
    if (!branch.protected) {
      const finding = node(
        'review-branch',
        'Review branch protection',
        'Incident',
        'threat',
        'The public legacy branch endpoint reported protected=false. Verify repository rulesets and organization controls before concluding the branch is unprotected.',
        'medium',
        { finding: true, verification_status: 'requires_review', rule: 'github-branch-review-v1' },
      );
      edge(
        root,
        finding,
        'HAS_REVIEW_FINDING',
        'Deterministic review rule based on the persisted GitHub branch observation.',
      );
      edge(
        finding,
        branchEvidence,
        'DERIVED_FROM',
        'Supporting branch endpoint observation. This is a review finding, not a proven vulnerability.',
      );
    }
    const selected = tree.tree
      .filter(
        (x) =>
          x.type === 'blob' &&
          /(^|\/)(package\.json|requirements\.txt|Dockerfile|docker-compose\.yml|SECURITY\.md|LICENSE)$|^\.github\/workflows\/[^/]+\.ya?ml$/.test(
            x.path,
          ),
      )
      .sort((a, b) => a.path.localeCompare(b.path))
      .slice(0, 35);
    const coverage = [
      `Public metadata only; commit ${branch.commit.sha}.`,
      `Contributor list bounded to 10 accounts; not an access-control inventory.`,
      'Legacy branch protection observation only; rulesets and organization policy are not evaluated.',
      `Tree ${tree.truncated ? 'truncated by GitHub' : 'returned by GitHub'}; at most 35 selected configuration/manifests modeled.`,
      'At most three manifests and 60 declared dependencies are inspected. Version ranges are not installed-version or vulnerability evidence.',
      'No cloud inventory, runtime telemetry, secret scanning, code scanning, or effective authorization evaluation.',
    ];
    let dependencyCount = 0,
      manifestCount = 0;
    for (const file of selected) {
      const fileId = node(
        'file:' + file.path,
        file.path,
        'Document',
        'infrastructure',
        `Configuration file present at commit ${branch.commit.sha}; blob ${file.sha}. File contents are not stored or executed.`,
        'none',
        {
          source_url: `https://github.com/${name}/blob/${branch.commit.sha}/${file.path}`,
          blob_sha: file.sha,
        },
      );
      edge(
        root,
        fileId,
        'CONTAINS',
        `GitHub git tree at ${branch.commit.sha}; path ${file.path}; blob ${file.sha}.`,
      );
      if (!/(^|\/)(package\.json|requirements\.txt)$/.test(file.path) || manifestCount >= 3)
        continue;
      manifestCount++;
      const content = z
        .object({
          encoding: z.literal('base64'),
          size: z.number().max(150000),
          content: z.string().max(220000),
          sha: z.literal(file.sha),
        })
        .parse(
          await this.get(
            base +
              '/contents/' +
              file.path.split('/').map(encodeURIComponent).join('/') +
              '?ref=' +
              branch.commit.sha,
            signal,
          ),
        );
      const text = Buffer.from(content.content, 'base64').toString('utf8');
      let dependencies: [string, string][] = [];
      if (file.path.endsWith('package.json')) {
        const manifest = z
          .object({
            dependencies: z.record(z.string(), z.string()).optional(),
            devDependencies: z.record(z.string(), z.string()).optional(),
          })
          .parse(JSON.parse(text));
        dependencies = Object.entries({ ...manifest.dependencies, ...manifest.devDependencies });
      } else {
        dependencies = text.split(/\r?\n/).flatMap((line) => {
          const match = line.match(
            /^([A-Za-z0-9_.-]+)\s*((?:==|~=|>=|<=|>|<)[A-Za-z0-9.*,+<>=~! -]+)\s*$/,
          );
          return match ? [[match[1], match[2]] as [string, string]] : [];
        });
      }
      for (const [pkg, version] of dependencies.sort(([a], [b]) => a.localeCompare(b))) {
        if (dependencyCount >= 60) break;
        if (
          !/^[@A-Za-z0-9_./-]{1,100}$/.test(pkg) ||
          !/^[A-Za-z0-9.*+|,^<>=~! -]{1,100}$/.test(version)
        )
          continue;
        dependencyCount++;
        const dep = node(
          'dependency:' + file.path + ':' + pkg,
          pkg,
          'Resource',
          'infrastructure',
          `Declared dependency ${version} in ${file.path}. Not a verified installed version or vulnerability.`,
          'none',
          { declared_version: version },
        );
        edge(
          fileId,
          dep,
          'DECLARES_DEPENDENCY',
          `Manifest blob ${file.sha}, commit ${branch.commit.sha}, declared range ${version}.`,
        );
      }
    }
    for (const [id, label] of [
      ['infrastructure', 'Repository & dependencies'],
      ['identity', 'Public identities'],
      ['threat', 'Review findings'],
      ['memory', 'Shared agent memory'],
      ['incident', 'Investigation evidence'],
    ]) {
      // Category identifiers are source-specific so multiple tenants/sources cannot collide.
      const group = node(
        'cluster:' + id,
        label,
        'Cluster',
        id,
        'Category of observed GitHub data; not a discovered security boundary.',
      );
      for (const n of nodes.filter((n) => n.type !== 'Cluster' && n.cluster === id))
        edge(n.id, group, 'MEMBER_OF', 'Visualization category; excluded from path discovery.');
    }
    const unique = [...new Map(nodes.map((n) => [n.id, n])).values()];
    const fingerprint = createHash('sha256')
      .update(
        JSON.stringify({
          head: branch.commit.sha,
          protected: branch.protected,
          files: selected,
          contributors: contributors.map((c) => c.login),
          version: 1,
        }),
      )
      .digest('hex');
    return {
      repository: name,
      head: branch.commit.sha,
      observed_at,
      fingerprint,
      nodes: unique,
      edges,
      coverage,
    };
  }
}
