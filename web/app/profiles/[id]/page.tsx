import { notFound } from "next/navigation";
import Dashboard from "@/components/Dashboard";

export default async function ProfilePage(props: PageProps<"/profiles/[id]">) {
  const { id } = await props.params;
  const { sweep } = await props.searchParams;
  if (!/^\d+$/.test(id)) notFound();
  // ?sweep=<id> follows a sweep that is already running (e.g. a second screen during the demo).
  const follow = typeof sweep === "string" && /^[0-9a-f-]{36}$/i.test(sweep) ? sweep : undefined;
  return <Dashboard profileId={Number(id)} followSweepId={follow} />;
}
