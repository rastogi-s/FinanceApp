import Link from "next/link";
import { PageTitle } from "@/components/page";
import { Button } from "@/components/ui/button";

export default function NotFound() {
  return (
    <>
      <PageTitle title="Nothing here" subtitle="That page doesn't exist." />
      <Button asChild>
        <Link href="/">Back to Home</Link>
      </Button>
    </>
  );
}
