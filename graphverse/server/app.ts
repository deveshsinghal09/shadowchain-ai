import express, { type ErrorRequestHandler } from 'express';
import helmet from 'helmet';
import { rateLimit } from 'express-rate-limit';
import { createHmac, randomBytes, timingSafeEqual, createHash } from 'node:crypto';
import { resolve } from 'node:path';
import { z } from 'zod';
import { HttpError, Repository } from './repository';
import type { Answer } from '../shared/types';
import type { LiveService } from './live';
import { repositoryName } from './github';
const id = z.string().regex(/^[a-zA-Z0-9_-]{1,100}$/);
const pathBody = z.object({ source: id, target: id, directed: z.boolean().default(true) }).strict();
export function createApp(
  repo: Repository,
  token: string,
  origins: string[],
  secure = false,
  explain: (answer: Answer) => Promise<Answer> = async (answer) => answer,
  publicRead = false,
  live?: LiveService,
  providerInfo: () => Promise<unknown> = async () => ({ configured: false }),
) {
  const app = express(),
    secret = randomBytes(32);
  const sign = (data: string) => createHmac('sha256', secret).update(data).digest('hex');
  const equal = (a: string, b: string) =>
    timingSafeEqual(
      createHash('sha256').update(a).digest(),
      createHash('sha256').update(b).digest(),
    );
  app.disable('x-powered-by');
  // Platform readiness reveals no graph content and exercises the real database.
  app.get('/health/ready', async (_req, res) => {
    await repo.db.query('RETURN 1 AS ready');
    res.setHeader('Cache-Control', 'no-store');
    res.json({ status: 'ready' });
  });
  app.use(
    helmet({
      contentSecurityPolicy: {
        directives: {
          defaultSrc: ["'self'"],
          scriptSrc: ["'self'"],
          styleSrc: ["'self'", "'unsafe-inline'"],
          imgSrc: ["'self'", 'data:', 'blob:'],
          fontSrc: ["'self'"],
          connectSrc: ["'self'"],
          workerSrc: ["'self'", 'blob:'],
          upgradeInsecureRequests: secure ? [] : null,
        },
      },
    }),
  );
  app.use('/api', (_req, res, next) => {
    res.setHeader('Cache-Control', 'no-store');
    next();
  });
  app.use('/api', express.json({ limit: '8kb' }));
  app.use(
    '/api',
    rateLimit({
      windowMs: 60000,
      limit: 120,
      standardHeaders: 'draft-8',
      legacyHeaders: false,
      handler: (_req, res) =>
        res.status(429).json({
          error: { code: 'rate_limited', message: 'Query limit reached. Retry in one minute.' },
        }),
    }),
  );
  app.use('/api', (req, _res, next) => {
    if (req.headers.origin && !origins.includes(req.headers.origin))
      return next(new HttpError(403, 'origin_denied', 'Origin is not allowed.'));
    if (!['GET', 'HEAD'].includes(req.method) && !req.is('application/json'))
      return next(new HttpError(415, 'content_type', 'Use application/json.'));
    next();
  });
  app.get('/api/health', (_req, res) => res.json({ status: 'ok' }));
  app.post(
    '/api/session',
    rateLimit({
      windowMs: 60000,
      limit: 8,
      handler: (_req, res) =>
        res
          .status(429)
          .json({ error: { code: 'rate_limited', message: 'Too many sign-in attempts.' } }),
    }),
    (req, res) => {
      const input = z
        .object({ token: z.string().min(32).max(512) })
        .strict()
        .parse(req.body);
      if (!equal(input.token, token))
        throw new HttpError(401, 'unauthorized', 'Operator token is invalid.');
      const data = String(Date.now() + 3600000),
        value = `${data}.${sign(data)}`;
      res
        .cookie('graphverse_session', value, {
          httpOnly: true,
          sameSite: 'strict',
          secure,
          maxAge: 3600000,
          path: '/api',
        })
        .json({ authenticated: true, expires_in: 3600 });
    },
  );
  app.use('/api', (req, res, next) => {
    const value =
      req.headers.cookie
        ?.split(';')
        .map((s) => s.trim())
        .find((s) => s.startsWith('graphverse_session='))
        ?.slice(19) ?? '';
    const [expiry, signature] = value.split('.');
    const operator =
      !!signature &&
      equal(signature, sign(expiry)) &&
      Number.isFinite(Number(expiry)) &&
      Number(expiry) >= Date.now();
    res.locals.operator = operator;
    // This allowlist only opens graph browsing. Saved investigations and every
    // graph mutation continue to require a valid operator session.
    const publicEndpoint =
      (req.method === 'GET' &&
        (/^\/(session|ready|graph|clusters|activity|integrations\/github)$/.test(req.path) ||
          /^\/nodes\/[a-zA-Z0-9_-]{1,100}(\/neighbors)?$/.test(req.path) ||
          /^\/graph\/snapshots\/[a-zA-Z0-9_-]{1,100}$/.test(req.path) ||
          req.path === '/graph/diff')) ||
      (req.method === 'POST' && ['/paths/shortest', '/ai/ask'].includes(req.path));
    if (!operator && !(publicRead && publicEndpoint))
      return next(new HttpError(401, 'unauthorized', 'Connect with your operator token.'));
    next();
  });
  app.get('/api/session', (_req, res) =>
    res.json({
      authenticated: res.locals.operator,
      access: res.locals.operator ? 'operator' : 'public',
      can_write: res.locals.operator,
      can_use_model: res.locals.operator,
      tenant: repo.tenant,
    }),
  );
  app.delete('/api/session', (_req, res) =>
    res.clearCookie('graphverse_session', { path: '/api' }).json({ authenticated: false }),
  );
  app.get('/api/ready', async (_req, res) => {
    await repo.db.query('RETURN 1 AS ready');
    res.json({ status: 'ready', database: 'FalkorDB' });
  });
  app.get('/api/graph', async (_req, res) => res.json(await repo.graph()));
  app.post('/api/provider/check', rateLimit({ windowMs: 60000, limit: 2 }), async (req, res) => {
    z.object({}).strict().parse(req.body);
    res.json(await providerInfo());
  });
  app.get('/api/integrations/github', async (_req, res) =>
    res.json(live ? await live.status() : { enabled: false, repositories: [], sources: [] }),
  );
  app.get('/api/activity', async (_req, res) =>
    res.json(live ? await live.activity() : { runs: [], tasks: [], handoffs: [] }),
  );
  app.get('/api/graph/snapshots/:id', async (req, res) => {
    if (!live)
      throw new HttpError(503, 'integration_disabled', 'GitHub integration is not configured.');
    res.json(await live.snapshot(id.parse(req.params.id)));
  });
  app.get('/api/graph/diff', async (req, res) => {
    if (!live)
      throw new HttpError(503, 'integration_disabled', 'GitHub integration is not configured.');
    const query = z.object({ before: id, after: id }).strict().parse(req.query);
    res.json(await live.difference(query.before, query.after));
  });
  app.post(
    '/api/integrations/github/sync',
    rateLimit({ windowMs: 60000, limit: 2 }),
    async (req, res) => {
      if (!live)
        throw new HttpError(503, 'integration_disabled', 'GitHub integration is not configured.');
      const body = z.object({ repository: repositoryName }).strict().parse(req.body);
      res.json(await live.sync(body.repository));
    },
  );
  app.post('/api/activity/:id/resume', async (req, res) => {
    if (!live)
      throw new HttpError(503, 'integration_disabled', 'Live investigations are not configured.');
    res.json(await live.resume(id.parse(req.params.id)));
  });
  app.post('/api/what-if/remove-relationship', async (req, res) => {
    const b = z.object({ source: id, target: id, edge_id: id }).strict().parse(req.body);
    res.json(await repo.simulate(b.source, b.target, b.edge_id));
  });
  app.post('/api/simulations/:id/review', async (req, res) => {
    const b = z
      .object({
        status: z.enum(['UNDER_REVIEW', 'APPROVED', 'REJECTED']),
        reason: z
          .string()
          .trim()
          .min(1)
          .max(500)
          .regex(/^[^\r\n]*$/)
          .refine(
            (value) =>
              !/(?:api[-_ ]?key|password|secret|token)\s*[:=]|-----BEGIN|gh[pousr]_|AIza|AKIA/i.test(
                value,
              ),
            'Credentials are not allowed in review notes.',
          ),
      })
      .strict()
      .parse(req.body);
    res.json(await repo.reviewSimulation(z.uuid().parse(req.params.id), b.status, b.reason));
  });
  app.get('/api/simulations', async (_req, res) => res.json(await repo.simulations()));
  app.get('/api/simulations/:id', async (req, res) =>
    res.json(await repo.simulation(z.uuid().parse(req.params.id))),
  );
  app.get('/api/nodes/:id', async (req, res) => res.json(await repo.node(id.parse(req.params.id))));
  app.get('/api/nodes/:id/neighbors', async (req, res) =>
    res.json(await repo.neighbors(id.parse(req.params.id))),
  );
  app.post('/api/paths/shortest', async (req, res) => {
    const b = pathBody.parse(req.body);
    res.json(await repo.shortest(b.source, b.target, b.directed));
  });
  app.post('/api/ai/ask', async (req, res) => {
    const b = z
      .object({
        question: z.string().trim().min(1).max(1000),
        source: id.optional(),
        target: id.optional(),
      })
      .strict()
      .parse(req.body);
    const answer = await repo.ask(b.question, b.source, b.target);
    // Anonymous questions remain graph-grounded and never call a paid provider.
    if (!res.locals.operator) return res.json(answer);
    try {
      res.json(await explain(answer));
    } catch (error) {
      if (!(error instanceof HttpError) || error.code !== 'model_unavailable') throw error;
      res.json({
        ...answer,
        model_note:
          'Gemini is temporarily unavailable. The answer and evidence below come directly from FalkorDB.',
      });
    }
  });
  app.get('/api/clusters', async (_req, res) => res.json(await repo.clusters()));
  app.get('/api/investigations', async (_req, res) => res.json(await repo.investigations()));
  app.get('/api/investigations/:id/path', async (req, res) =>
    res.json(await repo.investigationPath(id.parse(req.params.id))),
  );
  app.post('/api/investigations', async (req, res) => {
    const b = z
      .object({ id: z.uuid(), title: z.string().trim().min(1).max(120), source: id, target: id })
      .strict()
      .parse(req.body);
    res.status(201).json(await repo.saveInvestigation(b.id, b.title, b.source, b.target));
  });
  app.use('/api', (_req, _res, next) =>
    next(new HttpError(404, 'not_found', 'API endpoint not found.')),
  );
  app.use(express.static(resolve('dist'), { index: 'index.html' }));
  const errors: ErrorRequestHandler = (error, _req, res, _next) => {
    const validation = error instanceof z.ZodError;
    const malformed = error instanceof SyntaxError && 'body' in error;
    const bodyLimit = error?.type === 'entity.too.large';
    const known = error instanceof HttpError;
    const status = known ? error.status : validation || malformed ? 400 : bodyLimit ? 413 : 503;
    res.status(status).json({
      error: {
        code: known
          ? error.code
          : validation || malformed
            ? 'invalid_request'
            : bodyLimit
              ? 'payload_too_large'
              : 'database_unavailable',
        message: known
          ? error.message
          : validation || malformed
            ? 'Request fields are invalid.'
            : bodyLimit
              ? 'Request exceeds the 8 KB limit.'
              : 'FalkorDB is unavailable or the bounded query could not complete. Retry after checking the database.',
      },
    });
  };
  app.use(errors);
  return app;
}
