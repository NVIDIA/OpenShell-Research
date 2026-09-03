const START_REQUEST_ATTEMPTS = 3;
const START_RETRY_BASE_DELAY_MS = 250;

type RetryNotice = {
  nextAttempt: number;
  attempts: number;
  delayMilliseconds: number;
};

export async function retryAmbiguousStart<T>(
  operation: () => Promise<T>,
  shouldRetry: (error: unknown) => boolean,
  onRetry: (notice: RetryNotice) => void = () => undefined,
  sleep: (milliseconds: number) => Promise<void> = (milliseconds) =>
    new Promise<void>((resolve) => setTimeout(resolve, milliseconds)),
): Promise<T> {
  for (let attempt = 1; attempt <= START_REQUEST_ATTEMPTS; attempt += 1) {
    try {
      return await operation();
    } catch (error) {
      if (!shouldRetry(error) || attempt === START_REQUEST_ATTEMPTS) throw error;
      const delayMilliseconds = START_RETRY_BASE_DELAY_MS * (2 ** (attempt - 1));
      onRetry({
        nextAttempt: attempt + 1,
        attempts: START_REQUEST_ATTEMPTS,
        delayMilliseconds,
      });
      await sleep(delayMilliseconds);
    }
  }
  throw new Error("unreachable job creation retry state");
}
