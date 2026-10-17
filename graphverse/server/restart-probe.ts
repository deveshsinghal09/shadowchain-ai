import { Database } from './database';
import { Repository } from './repository';
const [mode, graph, id] = process.argv.slice(2);
if (!['write', 'read'].includes(mode) || !/^graphverse_test_[a-f0-9]+$/.test(graph))
  throw new Error('This probe requires an isolated test graph.');
const db = new Database(graph),
  repo = new Repository(db, 'test-one');
try {
  if (mode === 'write')
    await repo.saveInvestigation(id, 'Cross-process investigation', 'alice', 'vault');
  const path = await repo.investigationPath(id);
  console.log(JSON.stringify({ id, hops: path.edges.length }));
} finally {
  await db.close();
}
