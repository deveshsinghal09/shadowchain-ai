import { database } from './database';
import { seed } from './seed';
import { settings } from './config';
try {
  console.log(JSON.stringify(await seed(database, settings.GRAPHVERSE_TENANT)));
} finally {
  await database.close();
}
