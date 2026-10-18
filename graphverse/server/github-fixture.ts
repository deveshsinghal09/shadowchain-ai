import assert from 'node:assert/strict';
export const name = 'example/security-project',
  sha = 'a'.repeat(40),
  blob = 'b'.repeat(40);
export function transport(
  options: { private?: boolean; head?: string; protected?: boolean; fail?: boolean } = {},
): typeof fetch {
  return async (input, init) => {
    const url = new URL(String(input));
    assert.equal(url.origin, 'https://api.github.com');
    assert.equal(init?.method, 'GET');
    assert.equal(init?.redirect, 'error');
    if (options.fail) return new Response('upstream secret that must not leak', { status: 503 });
    const path = url.pathname;
    let body: unknown;
    if (path === '/repos/' + name)
      body = {
        id: 123,
        full_name: name,
        private: options.private ?? false,
        default_branch: 'main',
        owner: { login: 'example' },
      };
    else if (path.endsWith('/branches/main'))
      body = { protected: options.protected ?? false, commit: { sha: options.head ?? sha } };
    else if (path.includes('/git/trees/'))
      body = {
        truncated: false,
        tree: [
          { type: 'blob', path: 'package.json', sha: blob },
          { type: 'blob', path: 'LICENSE', sha: blob },
        ],
      };
    else if (path.endsWith('/contributors'))
      body = [{ login: 'example' }, { login: 'another-person' }];
    else if (path.endsWith('/contents/package.json'))
      body = {
        encoding: 'base64',
        sha: blob,
        size: 50,
        content: Buffer.from(
          JSON.stringify({
            dependencies: {
              express: '5.3.0',
              'do-not-import': 'https://token:password@example.invalid',
            },
          }),
        ).toString('base64'),
      };
    else throw new Error('Unexpected URL ' + path);
    return new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } });
  };
}
