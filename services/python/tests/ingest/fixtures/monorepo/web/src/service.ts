import type {
  Client,
} from "@example/client";
import "./polyfill";
export { helper } from "./helper";

export interface Repository {
  find(id: string): Promise<string>;
}
export class Service {
  async find(
    id: string,
  ): Promise<string> {
    return id;
  }
}
export const load = async (
  id: string,
): Promise<string> => id;
// function fakeMethod() {}