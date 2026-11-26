"use client";

import Link from "next/link";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, ShieldCheck, Sparkles } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

export default function HomePage() {
  const router = useRouter();
  const { user, loading } = useAuth();

  useEffect(() => {
    if (!loading && user) {
      router.replace("/dashboard");
    }
  }, [loading, user, router]);

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-background px-4 py-12">
      <div className="w-full max-w-lg space-y-6 text-center">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl bg-primary text-primary-foreground shadow-lg">
          <Sparkles className="h-6 w-6" />
        </div>
        <div className="space-y-2">
          <Badge variant="default">Phase 1 Foundation</Badge>
          <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">Metuse AI</h1>
          <p className="text-sm leading-relaxed text-muted-foreground sm:text-base">
            Multi-tenant Agency Operating System powered by FastAPI, PostgreSQL Row-Level Security,
            and Supabase managed infrastructure.
          </p>
        </div>

        <div className="flex flex-col justify-center gap-3 sm:flex-row">
          <Link href="/login">
            <Button size="lg" className="w-full sm:w-auto">
              Sign in to workspace
              <ArrowRight className="h-4 w-4" />
            </Button>
          </Link>
          <Link href="/register">
            <Button size="lg" variant="outline" className="w-full sm:w-auto">
              Create account
            </Button>
          </Link>
        </div>

        <p className="flex items-center justify-center gap-1.5 text-xs text-muted-foreground">
          <ShieldCheck className="h-3.5 w-3.5 text-emerald-400" />
          Tenant-isolated · Audit-logged · Zero frontend secrets
        </p>
      </div>
    </div>
  );
}
