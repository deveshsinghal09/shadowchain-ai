import { database } from './database';
import { settings, operatorToken } from './config';
import { Repository } from './repository';
import { createApp } from './app';
import { GeminiExplainer } from './gemini';
// Initialize schema only. Production startup never seeds synthetic records.
for (const label of ['GVEntity', 'GVInvestigation']) {
  try {
    await database.query(`CREATE INDEX ON :${label}(key)`, {}, true);
  } catch (error) {
    if (!(error instanceof Error && error.message === "Attribute 'key' is already indexed"))
      throw error;
  }
}
const explainer =
  settings.GRAPHVERSE_AI_PROVIDER === 'gemini'
    ? new GeminiExplainer(settings.GEMINI_API_KEY, settings.GEMINI_MODEL)
    : undefined;
const app = createApp(
  new Repository(database, settings.GRAPHVERSE_TENANT),
  operatorToken(),
  settings.GRAPHVERSE_ORIGINS.split(','),
  settings.GRAPHVERSE_SECURE_COOKIE === 'true',
  explainer ? (answer) => explainer.explain(answer) : undefined,
);
const server = app.listen(settings.PORT, settings.HOST, () =>
  console.log(
    JSON.stringify({
      event: 'graphverse_listening',
      host: settings.HOST,
      port: settings.PORT,
      graph: settings.GRAPHVERSE_GRAPH,
    }),
  ),
);
for (const signal of ['SIGINT', 'SIGTERM'])
  process.on(signal, () => {
    server.close(() => {
      void database.close().finally(() => process.exit(0));
    });
  });
