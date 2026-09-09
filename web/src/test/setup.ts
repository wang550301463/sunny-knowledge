import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach, vi } from 'vitest';
afterEach(() => { cleanup(); sessionStorage.clear(); });
Object.defineProperty(window, 'matchMedia', {value: vi.fn().mockImplementation(() => ({matches:false,addListener:vi.fn(),removeListener:vi.fn(),addEventListener:vi.fn(),removeEventListener:vi.fn(),dispatchEvent:vi.fn()}))});
class Observer { observe() {} unobserve() {} disconnect() {} }
globalThis.ResizeObserver = Observer;