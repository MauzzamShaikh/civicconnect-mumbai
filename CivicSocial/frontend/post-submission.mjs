export function createSubmissionController(onBusyChange = () => {}) {
  let busy = false;
  const notifyBusy = value => {
    try { onBusyChange(value); } catch { /* UI observers cannot strand submission state. */ }
  };
  return {
    get busy() { return busy; },
    async run(request) {
      if (busy) return { ok: false, duplicate: true };
      busy = true;
      notifyBusy(true);
      try {
        const value = await request();
        if (!value || typeof value !== "object" || !Number.isSafeInteger(Number(value.id)) || Number(value.id) < 1) {
          throw new Error("The server returned an unexpected post response. Your draft is still available; please retry.");
        }
        return { ok: true, value };
      } catch (error) {
        if (error?.name === "AbortError" || error?.name === "TimeoutError") {
          return { ok: false, error: new Error("The request timed out. Your draft is still available; please try again.") };
        }
        if (error instanceof TypeError) {
          return { ok: false, error: new Error("Could not reach CivicSocial. Check your connection and try again; your draft is still available.") };
        }
        return { ok: false, error: error instanceof Error ? error : new Error("Could not publish post. Please try again; your draft is still available.") };
      } finally {
        busy = false;
        notifyBusy(false);
      }
    },
  };
}
