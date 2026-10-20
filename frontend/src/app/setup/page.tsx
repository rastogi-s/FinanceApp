"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Enrollment } from "@/components/auth/enrollment";
import { useAuth } from "@/lib/auth";

export default function SetupPage() {
  const { state } = useAuth();
  const router = useRouter();
  useEffect(() => {
    if (state.status === "signed-out") router.replace("/signin");
  }, [state, router]);
  return <Enrollment mode="setup" />;
}
