"use client";

import React, { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { apiClient, ApiClientError } from "@/lib/api-client";
import { useAuth } from "@/lib/auth-context";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { Alert } from "@/components/ui/badge";

export default function ClientOnboardingPage() {
  const router = useRouter();
  const { activeWorkspace } = useAuth();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!activeWorkspace) {
    return (
      <div className="mx-auto max-w-xl py-4">
        <Card>
          <CardHeader>
            <CardTitle>Create a workspace first</CardTitle>
            <CardDescription>
              Clients belong to a tenant-isolated workspace. Create your workspace before onboarding clients.
            </CardDescription>
          </CardHeader>
          <CardFooter>
            <Link href="/onboarding/workspace">
              <Button>Go to Workspace Onboarding</Button>
            </Link>
          </CardFooter>
        </Card>
      </div>
    );
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await apiClient.createClient(activeWorkspace!.workspace_id, {
        name: name.trim(),
        slug: slug.trim() || undefined,
      });
      router.replace("/clients");
    } catch (err) {
      setError(err instanceof ApiClientError ? err.message : "Failed to create client.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto max-w-xl py-4">
      <Card>
        <CardHeader>
          <CardTitle>Onboard a Client</CardTitle>
          <CardDescription>
            Add a new client account inside <span className="text-foreground">{activeWorkspace.workspace_name}</span>.
          </CardDescription>
        </CardHeader>

        <form onSubmit={handleSubmit}>
          <CardContent className="space-y-4">
            {error && <Alert variant="destructive">{error}</Alert>}

            <div className="space-y-1.5">
              <Label htmlFor="clientName">Client Brand Name</Label>
              <Input
                id="clientName"
                required
                placeholder="Acme Coffee Roasters"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="clientSlug">Slug (optional)</Label>
              <Input
                id="clientSlug"
                placeholder="acme-coffee"
                value={slug}
                onChange={(e) => setSlug(e.target.value)}
              />
            </div>
          </CardContent>

          <CardFooter className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => router.back()}>
              Cancel
            </Button>
            <Button type="submit" disabled={submitting}>
              {submitting ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Creating client...
                </>
              ) : (
                "Create client"
              )}
            </Button>
          </CardFooter>
        </form>
      </Card>
    </div>
  );
}
