import { Database } from './database';
import { LiveService } from './live';
const [graph, tenant, repository] = process.argv.slice(2);
if (!/^graphverse_test_[a-f0-9]+$/.test(graph ?? ''))
  throw new Error('Only isolated test graphs are allowed.');
const db = new Database(graph);
try {
  const service = new LiveService(db, tenant, [repository]);
  await service.recover();
  console.log(JSON.stringify(await service.activity()));
} finally {
  await db.close();
}
