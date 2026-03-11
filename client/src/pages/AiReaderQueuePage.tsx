import { useAiReaderQueue } from "../hooks/useAiReaderQueue";
import { AiReaderQueueTable } from "../components/AiReaderQueueTable";

export function AiReaderQueuePage() {
  const { items, error } = useAiReaderQueue(true);
  return <AiReaderQueueTable items={items} error={error} />;
}
