import { FalkorDB } from 'falkordb';
import { settings } from './config';
// The official 6.8 client forwards connection errors before connect() returns an
// instance on which callers can register a listener. Contain that specific
// EventEmitter case; the rejected operation still reaches our 503 boundary.
const originalEmit = FalkorDB.prototype.emit;
FalkorDB.prototype.emit = function (event: string | symbol, ...args: unknown[]) {
  if (event === 'error' && this.listenerCount('error') === 0) return false;
  return originalEmit.call(this, event, ...args);
};
export class Database {
  private client?: Awaited<ReturnType<typeof FalkorDB.connect>>;
  private connecting?: Promise<Awaited<ReturnType<typeof FalkorDB.connect>>>;
  constructor(
    readonly name = settings.GRAPHVERSE_GRAPH,
    readonly endpoint = { host: settings.FALKORDB_HOST, port: settings.FALKORDB_PORT },
  ) {}
  async connect() {
    if (this.client) return this.client;
    const socket = { ...this.endpoint, connectTimeout: 2000, reconnectStrategy: false as const };
    this.connecting ??= FalkorDB.connect({
      socket,
      username: settings.FALKORDB_USERNAME,
      password: settings.FALKORDB_PASSWORD,
    });
    try {
      this.client = await this.connecting;
      return this.client;
    } finally {
      this.connecting = undefined;
    }
  }
  async query<T = Record<string, unknown>>(
    query: string,
    params: Record<string, unknown> = {},
    write = false,
  ): Promise<T[]> {
    const client = await this.connect();
    const graph = client.selectGraph(this.name);
    type QueryOptions = NonNullable<Parameters<typeof graph.query>[1]>;
    const options: QueryOptions = { params: params as QueryOptions['params'], TIMEOUT: 2000 };
    try {
      const result = await (write ? graph.query(query, options) : graph.roQuery(query, options));
      return (result.data ?? []) as T[];
    } catch (error) {
      if (!(await client.connection).isOpen) {
        await this.close();
      }
      throw error;
    }
  }
  async close() {
    if (this.client) {
      await this.client.close();
      this.client = undefined;
    }
  }
}
export const database = new Database();
