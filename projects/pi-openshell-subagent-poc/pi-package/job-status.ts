import type { ExternalJobHandle } from "pi-subagents/external-job-provider";

type BatchLoader = (jobIds: string[]) => Promise<ExternalJobHandle[]>;

type Waiter = {
  resolve: (handle: ExternalJobHandle) => void;
  reject: (error: unknown) => void;
};

export class JobStatusBatcher {
  private pending = new Map<string, Waiter[]>();
  private timer: ReturnType<typeof setTimeout> | undefined;
  private readonly load: BatchLoader;
  private readonly windowMilliseconds: number;

  constructor(load: BatchLoader, windowMilliseconds = 25) {
    this.load = load;
    this.windowMilliseconds = windowMilliseconds;
  }

  get(providerJobId: string): Promise<ExternalJobHandle> {
    return new Promise((resolve, reject) => {
      const waiters = this.pending.get(providerJobId) ?? [];
      waiters.push({ resolve, reject });
      this.pending.set(providerJobId, waiters);
      if (!this.timer) {
        this.timer = setTimeout(() => void this.flush(), this.windowMilliseconds);
      }
    });
  }

  private async flush(): Promise<void> {
    this.timer = undefined;
    const pending = this.pending;
    this.pending = new Map();
    const jobIds = [...pending.keys()];
    try {
      const handles = await this.load(jobIds);
      const byId = new Map(handles.map((handle) => [handle.providerJobId, handle]));
      for (const [jobId, waiters] of pending) {
        const handle = byId.get(jobId);
        if (!handle) {
          const error = new Error(`OpenShell Tool Service omitted job '${jobId}' from batch status`);
          for (const waiter of waiters) waiter.reject(error);
          continue;
        }
        for (const waiter of waiters) waiter.resolve(handle);
      }
    } catch (error) {
      for (const waiters of pending.values()) {
        for (const waiter of waiters) waiter.reject(error);
      }
    }
    if (this.pending.size > 0 && !this.timer) {
      this.timer = setTimeout(() => void this.flush(), this.windowMilliseconds);
    }
  }
}
