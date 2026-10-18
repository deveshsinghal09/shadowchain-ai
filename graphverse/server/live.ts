import { randomUUID } from 'node:crypto';
import type { Database } from './database';
import { HttpError } from './repository';
import { GitHubReader, githubId, repositoryName } from './github';

const agents = [
  ['cloud-sentinel', 'Cloud Sentinel', 'episodic'],
  ['identity-guardian', 'Identity Guardian', 'semantic'],
  ['risk-oracle', 'Risk Oracle', 'semantic'],
  ['memory-keeper', 'Memory Keeper', 'episodic'],
  ['coordinator', 'Coordinator', 'episodic'],
  ['response-architect', 'Response Architect', 'procedural'],
] as const;
export class LiveService {
  readonly repositories: string[];
  constructor(
    readonly db: Database,
    readonly tenant: string,
    repositories: string[],
    readonly reader = new GitHubReader(),
  ) {
    this.repositories = repositories
      .filter(Boolean)
      .map((x) => repositoryName.parse(x).toLowerCase());
  }
  async status() {
    const sources = await this.db.query<{ source: Record<string, unknown> }>(
      'MATCH (s:GVSource) WHERE s.tenant=$tenant RETURN properties(s) AS source ORDER BY s.repository LIMIT 5',
      { tenant: this.tenant },
    );
    return {
      enabled: this.repositories.length > 0,
      repositories: this.repositories,
      sources: sources.map(({ source }) => ({
        repository: source.repository,
        status: source.status,
        head: source.head,
        observed_at: source.observed_at,
        last_checked_at: source.last_checked_at,
        run_id: source.run_id,
        node_count: source.node_count,
        edge_count: source.edge_count,
        coverage: source.coverage,
        error: source.error,
      })),
      mode: 'read-only-public-github',
      automatic_sync: false,
    };
  }
  async activity() {
    const runs = await this.db.query<{ run: Record<string, unknown> }>(
      'MATCH (w:GVWorkflow) WHERE w.tenant=$tenant RETURN properties(w) AS run ORDER BY w.created_at DESC LIMIT 10',
      { tenant: this.tenant },
    );
    const tasks = await this.db.query<{ task: Record<string, unknown> }>(
      'MATCH (t:GVTask) WHERE t.tenant=$tenant RETURN properties(t) AS task ORDER BY t.created_at DESC,t.step LIMIT 60',
      { tenant: this.tenant },
    );
    const handoffs = await this.db.query<{ handoff: Record<string, unknown> }>(
      'MATCH (h:GVHandoff) WHERE h.tenant=$tenant RETURN properties(h) AS handoff ORDER BY h.created_at DESC LIMIT 50',
      { tenant: this.tenant },
    );
    return {
      runs: runs.map(({ run }) => ({
        id: run.id,
        status: run.status,
        cursor: run.cursor,
        repository: run.repository,
        head: run.head,
        created_at: run.created_at,
        updated_at: run.updated_at,
      })),
      tasks: tasks.map((x) => x.task),
      handoffs: handoffs.map((x) => x.handoff),
    };
  }
  async snapshot(runId: string) {
    const rows = await this.db.query<{ snapshot: string }>(
      'MATCH (w:GVWorkflow {key:$key}) WHERE w.tenant=$tenant RETURN w.snapshot_json AS snapshot',
      { key: `${this.tenant}:${runId}`, tenant: this.tenant },
    );
    if (!rows[0]?.snapshot)
      throw new HttpError(404, 'snapshot_not_found', 'Source snapshot not found.');
    return JSON.parse(rows[0].snapshot);
  }
  async difference(beforeId: string, afterId: string) {
    const [before, after] = await Promise.all([this.snapshot(beforeId), this.snapshot(afterId)]);
    if (before.repository !== after.repository)
      throw new HttpError(400, 'source_mismatch', 'Compare snapshots from the same repository.');
    const oldIds = new Set(before.nodes.map((n: { id: string }) => n.id)),
      newIds = new Set(after.nodes.map((n: { id: string }) => n.id));
    return {
      before: beforeId,
      after: afterId,
      source: before.repository,
      added_nodes: after.nodes.filter((n: { id: string }) => !oldIds.has(n.id)),
      removed_nodes: before.nodes.filter((n: { id: string }) => !newIds.has(n.id)),
      before_timestamp: before.snapshot_at,
      after_timestamp: after.snapshot_at,
      explanation:
        'Observed inventory difference. Observation order alone does not establish the cause of a security change.',
    };
  }
  async sync(input: string) {
    const repository = repositoryName.parse(input).toLowerCase();
    if (!this.repositories.includes(repository))
      throw new HttpError(
        403,
        'source_not_allowed',
        'This repository is not in the server source allowlist.',
      );
    const lease = randomUUID(),
      lock = `${this.tenant}:github-sync`,
      source = `github:${repository}`;
    const now = Date.now();
    const claimed = await this.db.query(
      `MERGE (l:GVSyncLock {key:$key}) ON CREATE SET l.until=0
      WITH l WHERE l.until < $now SET l.until=$until,l.lease=$lease RETURN l.key AS key`,
      { key: lock, now, until: now + 180000, lease },
      true,
    );
    if (!claimed.length)
      throw new HttpError(409, 'sync_in_progress', 'A source synchronization is already running.');
    try {
      const inventory = await this.reader.inventory(repository);
      const prior = await this.db.query<{ fingerprint: string; run_id: string }>(
        'MATCH (s:GVSource {key:$key}) WHERE s.tenant=$tenant RETURN s.fingerprint AS fingerprint,s.run_id AS run_id',
        { key: `${this.tenant}:${source}`, tenant: this.tenant },
      );
      // A revert to an older source state is a new observation, not a replay of
      // an old closed validity interval. Unchanged retries use prior.run_id below.
      const runId = githubId(
        this.tenant +
          ':' +
          source +
          ':' +
          inventory.fingerprint +
          ':' +
          (prior[0]?.run_id ?? 'initial'),
      );
      if (prior[0]?.fingerprint === inventory.fingerprint) {
        await this.db.query(
          'MATCH (s:GVSource {key:$key}) SET s.last_checked_at=$at,s.status="READY",s.error="" RETURN s.key AS key',
          { key: `${this.tenant}:${source}`, at: inventory.observed_at },
          true,
        );
        await this.resume(prior[0].run_id);
        return { duplicate: true, run_id: prior[0].run_id, ...(await this.status()) };
      }
      const existing = await this.db.query<{ count: number }>(
        'MATCH (n:GVEntity) WHERE n.tenant=$tenant AND coalesce(n.current,true)=true AND coalesce(n.source_key,"")<>$source RETURN count(n) AS count',
        { tenant: this.tenant, source },
      );
      if ((existing[0]?.count ?? 0) + inventory.nodes.length + 12 > 500)
        throw new HttpError(
          413,
          'graph_limit',
          'This import would exceed the bounded explorer inventory.',
        );
      const result = await this.db.query(
        `MATCH (l:GVSyncLock {key:$lock}) WHERE l.lease=$lease
        OPTIONAL MATCH (old:GVEntity) WHERE old.tenant=$tenant AND old.source_key=$source
        SET old.current=false WITH DISTINCT l
        OPTIONAL MATCH ()-[old:GVREL]->() WHERE old.tenant=$tenant AND old.source_key=$source AND old.valid_to IS NULL
        SET old.valid_to=$at WITH DISTINCT l
        UNWIND $nodes AS input MERGE (n:GVEntity {key:$tenant+':'+input.id})
        ON CREATE SET n.first_seen_at=$at
        SET n+=input,n.tenant=$tenant,n.source_key=$source,n.current=true,n.updated_at=$at,n.created_at=n.first_seen_at
        WITH DISTINCT l UNWIND $edges AS input
        MATCH (a:GVEntity {key:$tenant+':'+input.source}), (b:GVEntity {key:$tenant+':'+input.target})
        MERGE (a)-[r:GVREL {id:input.id+'_'+$run,tenant:$tenant}]->(b)
        ON CREATE SET r+=input,r.id=input.id+'_'+$run,r.source_key=$source,r.source_event_id=$run
        WITH DISTINCT l MERGE (s:GVSource {key:$tenant+':'+$source})
        SET s.tenant=$tenant,s.repository=$repository,s.fingerprint=$fingerprint,s.head=$head,
          s.observed_at=$at,s.last_checked_at=$at,s.run_id=$run,s.node_count=$node_count,
          s.edge_count=$edge_count,s.coverage=$coverage,s.status='READY',s.error=''
        MERGE (w:GVWorkflow {key:$tenant+':'+$run}) ON CREATE SET w.id=$run,w.tenant=$tenant,
          w.source_key=$source,w.repository=$repository,w.head=$head,w.snapshot_json=$snapshot,w.cursor=0,w.status='PENDING',w.created_at=$at,w.updated_at=$at
        RETURN w.id AS id`,
        {
          lock,
          lease,
          tenant: this.tenant,
          source,
          at: inventory.observed_at,
          nodes: inventory.nodes,
          edges: inventory.edges,
          run: runId,
          repository,
          fingerprint: inventory.fingerprint,
          head: inventory.head,
          node_count: inventory.nodes.length,
          edge_count: inventory.edges.length,
          coverage: inventory.coverage,
          snapshot: JSON.stringify({
            ...inventory,
            edges: inventory.edges.map((e) => ({ ...e, id: e.id + '_' + runId })),
            dataset: 'github-live-v1',
            snapshot_at: inventory.observed_at,
            truncated: false,
          }),
        },
        true,
      );
      if (!result.length)
        throw new HttpError(409, 'sync_lease_lost', 'Synchronization ownership changed. Retry.');
      await this.resume(runId);
      return { duplicate: false, run_id: runId, ...(await this.status()) };
    } catch (error) {
      // Failures never replace a last good graph or leak upstream response bodies.
      throw error;
    } finally {
      await this.db.query(
        'MATCH (l:GVSyncLock {key:$key}) WHERE l.lease=$lease SET l.until=0,l.lease="" RETURN l.key AS key',
        { key: lock, lease },
        true,
      );
    }
  }
  async resume(runId: string, maxSteps = 6) {
    for (let iteration = 0; iteration < maxSteps; iteration++) {
      const rows = await this.db.query<{
        cursor: number;
        source_key: string;
        repository: string;
        head: string;
        active_run: string;
      }>(
        `MATCH (w:GVWorkflow {key:$key}), (s:GVSource) WHERE w.tenant=$tenant AND s.tenant=$tenant AND s.key=$tenant+':'+w.source_key RETURN w.cursor AS cursor,w.source_key AS source_key,w.repository AS repository,w.head AS head,s.run_id AS active_run`,
        { key: `${this.tenant}:${runId}`, tenant: this.tenant },
      );
      if (!rows[0]) throw new HttpError(404, 'run_not_found', 'Workflow run not found.');
      const w = rows[0];
      if (w.cursor >= agents.length) break;
      if (w.active_run !== runId) {
        await this.db.query(
          'MATCH (w:GVWorkflow {key:$key}) WHERE w.tenant=$tenant SET w.status="SUPERSEDED",w.cursor=6 RETURN w.id AS id',
          { key: `${this.tenant}:${runId}`, tenant: this.tenant },
          true,
        );
        break;
      }
      const [agentId, agentName, memoryType] = agents[w.cursor],
        root = githubId(w.repository + ':repository');
      const observations = await this.db.query<{
        id: string;
        type: string;
        label: string;
        finding: boolean;
      }>(
        'MATCH (n:GVEntity) WHERE n.tenant=$tenant AND n.source_key=$source AND n.current=true RETURN n.id AS id,n.type AS type,n.label AS label,n.finding AS finding ORDER BY n.id LIMIT 350',
        { tenant: this.tenant, source: w.source_key },
      );
      // Actual graph traversal retrieves both current and previous agent observations.
      const memories = await this.db.query<{ id: string }>(
        `MATCH (a:GVEntity)-[r:GVREL]->(m:GVEntity)-[e:GVREL]->(asset:GVEntity {key:$root})
        WHERE a.tenant=$tenant AND m.tenant=$tenant AND asset.tenant=$tenant AND r.tenant=$tenant AND e.tenant=$tenant
          AND r.type='REMEMBERS' AND e.type='ABOUT' AND m.type='AgentMemory'
        RETURN DISTINCT m.id AS id ORDER BY m.id LIMIT 30`,
        { tenant: this.tenant, root: `${this.tenant}:${root}` },
      );
      const findings = await this.db.query<{ id: string; evidence: string }>(
        `MATCH p=(repo:GVEntity {key:$root})-[:GVREL*1..2]->(e:GVEntity)
        WHERE all(n IN nodes(p) WHERE n.tenant=$tenant) AND all(r IN relationships(p) WHERE r.tenant=$tenant AND r.valid_to IS NULL AND r.type IN ['HAS_REVIEW_FINDING','DERIVED_FROM'])
        RETURN DISTINCT e.id AS id,e.description AS evidence ORDER BY e.id LIMIT 30`,
        { tenant: this.tenant, root: `${this.tenant}:${root}` },
      );
      const inputs = observations
        .filter((n) => n.type === 'Document')
        .map((n) => n.id)
        .slice(0, 40);
      const findingCount = observations.filter((n) => n.finding).length;
      const summaries = [
        `Captured ${inputs.length} GitHub configuration/evidence records at commit ${w.head}. No AWS requests were made.`,
        `Observed ${observations.filter((n) => n.type === 'Identity').length} public owner/contributor identities. Contributor status does not establish access permissions.`,
        `Cypher retrieved ${findings.length} review/evidence nodes. ${findingCount} review findings require human verification; no exploitable access is asserted.`,
        `Retrieved ${memories.length} graph-linked memories for this repository. Earlier observations remain stored with their source commit.`,
        `Persisted sequential task ownership and handoffs for this six-stage run. Completed steps are guarded by the graph cursor.`,
        findingCount
          ? 'Review the persisted branch observation, check repository rulesets and organization policy, then decide whether a protection change is appropriate. No changes are executed.'
          : 'Retain the evidence snapshot, review coverage limits, and synchronize again after repository changes. No security remediation is inferred without evidence.',
      ];
      const at = new Date().toISOString(),
        taskId = githubId(runId + ':task:' + w.cursor),
        memoryId = githubId(runId + ':memory:' + w.cursor),
        actor = githubId(w.repository + ':agent:' + agentId);
      const changed = await this.db.query(
        `MATCH (w:GVWorkflow {key:$runKey}), (active:GVSource) WHERE w.tenant=$tenant AND w.cursor=$step AND active.tenant=$tenant AND active.key=$tenant+':'+w.source_key AND active.run_id=$run
        MATCH (asset:GVEntity {key:$root}) WHERE asset.tenant=$tenant
        MERGE (a:GVEntity {key:$tenant+':'+$actor}) ON CREATE SET a.id=$actor,a.created_at=$at
        SET a.tenant=$tenant,a.label=$agentName,a.type='Agent',a.cluster='memory',a.description='Deterministic GitHub investigation stage; no command or permission executor.',a.severity='none',a.source=$source,a.source_key=$source,a.synthetic=false,a.current=true
        MERGE (m:GVEntity {key:$tenant+':'+$memory}) ON CREATE SET m.id=$memory,m.created_at=$at
        SET m.tenant=$tenant,m.label=$agentName+' observation',m.type='AgentMemory',m.cluster='memory',m.description=$summary,m.severity='none',m.source=$source,m.source_key=$source,m.synthetic=false,m.current=true,m.memory_type=$memoryType,m.verification_status='observed',m.confidence=1,m.run_id=$run,m.input_evidence_ids=$inputs,m.retrieved_memory_ids=$retrieved,m.head=w.head
        MERGE (a)-[:GVREL {id:$task+'_remembers',tenant:$tenant,type:'REMEMBERS',source:$actor,target:$memory,source_key:$source,evidence:'Persisted stage output',confidence:1,valid_from:$at}]->(m)
        MERGE (m)-[:GVREL {id:$task+'_about',tenant:$tenant,type:'ABOUT',source:$memory,target:$rootId,source_key:$source,evidence:'Repository subject reference',confidence:1,valid_from:$at}]->(asset)
        MERGE (t:GVTask {key:$tenant+':'+$task}) ON CREATE SET t.id=$task,t.created_at=$at
        SET t.tenant=$tenant,t.run_id=$run,t.agent_id=$agentId,t.agent_name=$agentName,t.step=$step,t.status='COMPLETED',t.output=$summary,t.input_evidence_ids=$inputs,t.retrieved_memory_ids=$retrieved,t.output_memory_id=$memory,t.updated_at=$at
        MERGE (w)-[:GVTASK {tenant:$tenant}]->(t)
        MERGE (h:GVHandoff {key:$tenant+':'+$task}) ON CREATE SET h.id=$task,h.created_at=$at
        SET h.tenant=$tenant,h.run_id=$run,h.from_agent=$agentId,h.to_agent=$nextAgent,h.memory_id=$memory,h.status='DELIVERED',h.updated_at=$at
        MERGE (t)-[:GVHANDOFF {tenant:$tenant}]->(h)
        SET w.cursor=$step+1,w.updated_at=$at,w.status=CASE WHEN $step=5 THEN 'COMPLETED' ELSE 'RUNNING' END
        WITH m,t UNWIND $inputs AS evidenceId MATCH (e:GVEntity {key:$tenant+':'+evidenceId}) WHERE e.tenant=$tenant
        MERGE (m)-[:GVREL {id:$task+'_evidence_'+evidenceId,tenant:$tenant,type:'DERIVED_FROM',source:$memory,target:evidenceId,source_key:$source,evidence:'Stage input evidence at source commit',confidence:1,valid_from:$at}]->(e)
        RETURN DISTINCT t.id AS id`,
        {
          tenant: this.tenant,
          runKey: `${this.tenant}:${runId}`,
          step: w.cursor,
          root: `${this.tenant}:${root}`,
          rootId: root,
          actor,
          agentName,
          agentId,
          memory: memoryId,
          task: taskId,
          source: w.source_key,
          summary: summaries[w.cursor],
          memoryType,
          inputs,
          retrieved: memories.map((x) => x.id),
          at,
          run: runId,
          nextAgent: agents[w.cursor + 1]?.[0] ?? 'human-review',
        },
        true,
      );
      if (!changed.length) continue; // Another worker completed this guarded operation.
    }
    return this.activity();
  }
  async recover() {
    const rows = await this.db.query<{ id: string }>(
      'MATCH (w:GVWorkflow) WHERE w.tenant=$tenant AND w.cursor<6 RETURN w.id AS id ORDER BY w.created_at LIMIT 10',
      { tenant: this.tenant },
    );
    for (const row of rows) await this.resume(row.id);
  }
}
