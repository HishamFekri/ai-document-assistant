/** Ownership is independent of transport cancellation: late results may still arrive. */
export class RequestScope {
  constructor(readonly key = "") {}
  private active = true;
  private requests = new Map<string, AbortController>();

  activate() { this.active = true; }
  isActive() { return this.active; }
  cancel(channel: string) {
    const controller = this.requests.get(channel);
    this.requests.delete(channel);
    controller?.abort();
  }
  dispose() {
    this.active = false;
    for (const channel of this.requests.keys()) this.cancel(channel);
  }
  begin(channel: string) {
    this.cancel(channel);
    const controller = new AbortController();
    if (this.active) this.requests.set(channel, controller);
    else controller.abort();
    const current = () => this.active && !controller.signal.aborted
      && this.requests.get(channel) === controller;
    return {
      signal: controller.signal,
      current,
      finish: () => { if (current()) this.requests.delete(channel); },
    };
  }
}
