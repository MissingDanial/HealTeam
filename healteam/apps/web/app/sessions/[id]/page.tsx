import SessionWorkspace from "../../components/SessionWorkspace";

export default async function SessionPage({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<{ tab?: string }> }) {
  const { id } = await params;
  const query = await searchParams;
  return <SessionWorkspace key={id} sessionId={id} initialTab={query.tab === "report" ? "report" : "conversation"} />;
}
