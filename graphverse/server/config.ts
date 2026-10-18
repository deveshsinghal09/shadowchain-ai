import { config } from 'dotenv';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { z } from 'zod';
config({ path: resolve('.env.local'), quiet: true });
config({ path: resolve('../.env'), quiet: true });
const parsed = z
  .object({
    PORT: z.coerce.number().int().min(1024).max(65535).default(3100),
    HOST: z.string().default('127.0.0.1'),
    GRAPHVERSE_GRAPH: z
      .string()
      .regex(/^[a-zA-Z0-9_]{1,80}$/)
      .default('graphverse_demo_v1'),
    GRAPHVERSE_TENANT: z.string().min(1).max(100).default('graphverse-synthetic'),
    GRAPHVERSE_ORIGINS: z
      .string()
      .default('http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:3100'),
    GRAPHVERSE_TOKEN_FILE: z.string().default(''),
    GRAPHVERSE_OPERATOR_TOKEN: z.string().default(''),
    GRAPHVERSE_SECURE_COOKIE: z.enum(['true', 'false']).default('false'),
    GRAPHVERSE_PUBLIC_READ: z.enum(['true', 'false']).default('false'),
    GRAPHVERSE_DATASET: z.string().min(1).max(100).default('graphverse-synthetic-1.0'),
    GRAPHVERSE_GITHUB_REPOSITORIES: z.string().default(''),
    GITHUB_READ_TOKEN: z.string().default(''),
    GEMINI_API_KEY: z.string().default(''),
    GEMINI_MODEL: z
      .string()
      .regex(/^[a-zA-Z0-9.-]{1,80}$/)
      .default('gemini-3.1-flash-lite'),
    GRAPHVERSE_AI_PROVIDER: z.enum(['deterministic', 'gemini']).default('deterministic'),
    FALKORDB_HOST: z.string().default('127.0.0.1'),
    FALKORDB_PORT: z.coerce.number().int().min(1).max(65535).default(16379),
    FALKORDB_USERNAME: z.string().default('default'),
    FALKORDB_PASSWORD: z.string().min(1),
  })
  .safeParse(process.env);
if (!parsed.success)
  throw new Error('Invalid server configuration. Check required variable names and formats.');
export const settings = parsed.data;
if (
  process.env.NODE_ENV === 'production' &&
  (!process.env.GRAPHVERSE_GRAPH ||
    !process.env.GRAPHVERSE_TENANT ||
    !process.env.GRAPHVERSE_DATASET ||
    settings.GRAPHVERSE_SECURE_COOKIE !== 'true')
)
  throw new Error('Production requires explicit graph, tenant, dataset and secure cookies.');
if (settings.GRAPHVERSE_AI_PROVIDER === 'gemini' && !settings.GEMINI_API_KEY)
  throw new Error('Gemini requires a server-side GEMINI_API_KEY.');
export function operatorToken() {
  const token =
    settings.GRAPHVERSE_OPERATOR_TOKEN ||
    readFileSync(settings.GRAPHVERSE_TOKEN_FILE, 'utf8').trim();
  if (token.length < 32 || token.length > 512)
    throw new Error('Configure a private operator token file.');
  return token;
}
