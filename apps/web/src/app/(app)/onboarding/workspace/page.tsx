"use client";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import { Building2, Loader2, User } from "lucide-react";
import { apiClient, ApiClientError } from "@/lib/api-client";
import { useAuth } from "@/lib/auth-context";
import type { WorkspaceType } from "@/lib/types";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { Alert } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export default function WorkspaceOnboardingPage() {
  const router = useRouter();
  const { refreshSession, setActiveWorkspaceId } = useAuth();
  const [name, setName] = useState("");
  const [workspaceType, setWorkspaceType] = useState<WorkspaceType>("agency");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const ws = await apiClient.createWorkspace({
        name: name.trim(),
        workspace_type: workspaceType,
      });
      setActiveWorkspaceId(ws.id);
      await refreshSession();
      router.replace("/dashboard");
    } catch (err) {
      setError(
        err instanceof ApiClientError ? err.message : "Failed to create workspace."
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto max-w-xl py-4">
      <Card>
        <CardHeader>
          <CardTitle>Create a Workspace</CardTitle>
          <CardDescription>
            Choose Agency mode (multi-client, up to 5 members) or Creator mode (solo personal brand).
          </CardDescription>
        </CardHeader>

        <form onSubmit={handleSubmit}>
          <CardContent className="space-y-5">
            {error && <Alert variant="destructive">{error}</Alert>}

            <div className="grid gap-3 sm:grid-cols-2">
              <button
                type="button"
                onClick={() => setWorkspaceType("agency")}
                className={cn(
                  "rounded-lg border p-4 text-left transition-colors",
                  workspaceType === "agency"
                    ? "border-primary bg-primary/10"
                    : "border-border hover:bg-accent/50"
                )}
              >
                <Building2 className="h-5 w-5 text-indigo-400" />
                <p className="mt-2 text-sm font-semibold">Agency Workspace</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Manage multiple clients, team roles, and client reviewers (up to 5 members).
                </p>
              </button>

              <button
                type="button"
                onClick={() => setWorkspaceType("creator")}
                className={cn(
                  "rounded-lg border p-4 text-left transition-colors",
                  workspaceType === "creator"
                    ? "border-primary bg-primary/10"
                    : "border-border hover:bg-accent/50"
                )}
              >
                <User className="h-5 w-5 text-indigo-400" />
                <p className="mt-2 text-sm font-semibold">Creator Workspace</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Solo personal brand with auto-provisioned primary brand profile.
                </p>
              </button>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="workspaceName">Workspace Name</Label>
              <Input
                id="workspaceName"
                required
                placeholder={workspaceType === "agency" ? "Northstar Growth Studio" : "Alex Creates"}
                value={name}
                onChange={(e) => setName(e.target.value)}
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
                  Creating...
                </>
              ) : (
                "Create workspace"
              )}
            </Button>
          </CardFooter>
        </form>
      </Card>
    </div>
  );
}
