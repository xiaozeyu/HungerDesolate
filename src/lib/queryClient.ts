import { computed, onBeforeUnmount, shallowRef, watch, type Ref } from "vue";
import { describeError, type UserError } from "./commandError";

export interface QueryState<T> {
  data: T | null;
  error: UserError | null;
  loading: boolean;
  stale: boolean;
  updatedAt: number | null;
}

interface CacheEntry {
  data: unknown;
  updatedAt: number;
}

const cache = new Map<string, CacheEntry>();
const inFlight = new Map<string, Promise<unknown>>();

export const queryClient = {
  get(key: string): CacheEntry | undefined {
    return cache.get(key);
  },
  set<T>(key: string, data: T): void {
    cache.set(key, { data, updatedAt: Date.now() });
  },
  invalidate(prefix = ""): void {
    for (const key of cache.keys()) {
      if (key.startsWith(prefix)) cache.delete(key);
    }
  },
  clear(): void {
    cache.clear();
  },
};

export function useQuery<T>(key: Ref<string | null>, fetcher: (key: string) => Promise<T>) {
  const state = shallowRef<QueryState<T>>({ data: null, error: null, loading: false, stale: false, updatedAt: null });
  let sequence = 0;
  let disposed = false;

  function reset() {
    state.value = { data: null, error: null, loading: false, stale: false, updatedAt: null };
  }

  function hydrate(queryKey: string) {
    const entry = cache.get(queryKey);
    state.value = entry
      ? { data: entry.data as T, error: null, loading: false, stale: false, updatedAt: entry.updatedAt }
      : { data: null, error: null, loading: false, stale: false, updatedAt: null };
    return entry;
  }

  async function run(force = false) {
    const queryKey = key.value;
    if (!queryKey) return;
    const token = ++sequence;
    const cached = cache.get(queryKey);
    state.value = {
      data: cached?.data as T ?? null,
      error: null,
      loading: true,
      stale: false,
      updatedAt: cached?.updatedAt ?? null,
    };
    let promise = inFlight.get(queryKey);
    if (!promise || force) {
      promise = fetcher(queryKey);
      inFlight.set(queryKey, promise);
    }
    try {
      const data = (await promise) as T;
      if (disposed || token !== sequence || key.value !== queryKey) return;
      queryClient.set(queryKey, data);
      state.value = { data, error: null, loading: false, stale: false, updatedAt: Date.now() };
    } catch (cause) {
      if (disposed || token !== sequence || key.value !== queryKey) return;
      state.value = { ...state.value, error: describeError(cause), loading: false, stale: !!cached };
    } finally {
      if (inFlight.get(queryKey) === promise) inFlight.delete(queryKey);
    }
  }

  watch(key, queryKey => {
    if (!queryKey) {
      sequence++;
      reset();
      return;
    }
    hydrate(queryKey);
    void run(false);
  }, { immediate: true });

  onBeforeUnmount(() => {
    disposed = true;
    sequence++;
  });

  return {
    data: computed(() => state.value.data),
    error: computed(() => state.value.error),
    loading: computed(() => state.value.loading),
    stale: computed(() => state.value.stale),
    updatedAt: computed(() => state.value.updatedAt),
    refresh: () => run(true),
  };
}
