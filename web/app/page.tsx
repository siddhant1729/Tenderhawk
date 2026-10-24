import OpenProfile from "@/components/OpenProfile";
import ProfileForm from "@/components/ProfileForm";

export default function Home() {
  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col gap-2">
        <h1 className="text-3xl font-bold tracking-tight">Find the government tenders you can actually win.</h1>
        <p className="max-w-3xl text-ink-soft">
          Fill in your profile once. TinyFish agents sweep GeM, CPPP and Haryana e-procurement, read each bid’s
          eligibility rules, and tell you which ones you qualify for, with the exact clause behind every verdict.
        </p>
      </section>
      <OpenProfile />
      <ProfileForm />
    </div>
  );
}
