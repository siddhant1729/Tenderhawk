import { notFound } from "next/navigation";
import MatchView from "@/components/MatchView";

export default async function MatchPage(props: PageProps<"/matches/[id]">) {
  const { id } = await props.params;
  if (!/^\d+$/.test(id)) notFound();
  return <MatchView key={id} matchId={Number(id)} />;
}
