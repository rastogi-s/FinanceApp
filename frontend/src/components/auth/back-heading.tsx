import { ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";

export function BackHeading({ title, onBack }: { title: string; onBack: () => void }) {
  return (
    <div className="flex items-center gap-2">
      <Button type="button" variant="ghost" size="icon" className="-ml-2" onClick={onBack} aria-label="Back">
        <ArrowLeft />
      </Button>
      <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
    </div>
  );
}
