"use client";

import React, { useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Building2,
  FolderKanban,
  LayoutDashboard,
  LogOut,
  Menu,
  PlusCircle,
  ShieldCheck,
  Sparkles,
  Users,
  X,
} from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { href: "/dashboard", label: "Overview", icon: LayoutDashboard },
  { href: "/clients", label: "Clients", icon: Users },
  { href: "/jobs", label: "AI Workers", icon: FolderKanban },
  { href: "/onboarding/workspace", label: "New Workspace", icon: Building2 },
  { href: "/onboarding/client", label: "New Client", icon: PlusCircle },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, workspaces, activeWorkspace, setActiveWorkspaceId, signOut } = useAuth();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);

  async function handleSignOut() {
    setSigningOut(true);
    try {
      await signOut();
      router.replace("/login");
    } finally {
      setSigningOut(false);
    }
  }

  const sidebarContent = (
    <div className="flex h-full flex-col justify-between">
      <div className="space-y-6">
        <div className="flex items-center justify-between px-2">
          <Link href="/dashboard" className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-sm">
              <Sparkles className="h-4 w-4" />
            </div>
            <div>
              <p className="text-sm font-semibold tracking-tight">Metuse AI</p>
              <p className="text-[11px] text-muted-foreground">Agency OS · Phase 2</p>
            </div>
          </Link>
          <button
            type="button"
            onClick={() => setMobileOpen(false)}
            className="rounded-md p-1 text-muted-foreground hover:bg-accent lg:hidden"
            aria-label="Close navigation"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-1.5 px-2">
          <p className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
            Active Workspace
          </p>
          {workspaces.length > 0 ? (
            <select
              aria-label="Select workspace"
              value={activeWorkspace?.workspace_id ?? ""}
              onChange={(e) => setActiveWorkspaceId(e.target.value)}
              className="w-full rounded-md border border-border bg-background px-2.5 py-2 text-xs font-medium text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            >
              {workspaces.map((ws) => (
                <option key={ws.workspace_id} value={ws.workspace_id}>
                  {ws.workspace_name} ({ws.workspace_type})
                </option>
              ))}
            </select>
          ) : (
            <Link
              href="/onboarding/workspace"
              className="block rounded-md border border-dashed border-border px-3 py-2 text-xs text-muted-foreground hover:border-primary hover:text-foreground"
            >
              + Create first workspace
            </Link>
          )}
        </div>

        <nav className="space-y-1 px-1" aria-label="Primary navigation">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={() => setMobileOpen(false)}
                className={cn(
                  "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition-colors",
                  active
                    ? "bg-primary/15 text-indigo-300"
                    : "text-muted-foreground hover:bg-accent hover:text-foreground"
                )}
              >
                <Icon className="h-4 w-4 shrink-0" />
                <span>{item.label}</span>
              </Link>
            );
          })}
        </nav>

        <div className="mx-2 rounded-lg border border-border bg-secondary/40 p-3">
          <div className="flex items-center gap-2 text-xs font-medium text-foreground">
            <FolderKanban className="h-3.5 w-3.5 text-indigo-400" />
            <span>Phase 2+ Modules</span>
          </div>
          <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
            Research, SEO/AEO, Media Studio, Meta Publishing & Analytics unlock after Phase 1 foundation sign-off.
          </p>
        </div>
      </div>

      <div className="space-y-3 border-t border-border pt-4">
        <div className="flex items-center justify-between px-2">
          <div className="min-w-0">
            <p className="truncate text-xs font-medium text-foreground">
              {user?.full_name || user?.email}
            </p>
            <p className="truncate text-[11px] text-muted-foreground">{user?.email}</p>
          </div>
          <Badge variant="outline" className="shrink-0 text-[10px]">
            {activeWorkspace?.role_name ?? "member"}
          </Badge>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="w-full justify-start"
          onClick={handleSignOut}
          disabled={signingOut}
        >
          <LogOut className="h-3.5 w-3.5" />
          {signingOut ? "Signing out..." : "Sign out"}
        </Button>
      </div>
    </div>
  );

  return (
    <div className="flex min-h-screen bg-background text-foreground">
      {/* Desktop Sidebar */}
      <aside className="hidden w-64 shrink-0 border-r border-border bg-card/70 p-4 lg:block">
        {sidebarContent}
      </aside>

      {/* Mobile Drawer */}
      {mobileOpen && (
        <div className="fixed inset-0 z-50 flex lg:hidden">
          <div
            className="fixed inset-0 bg-black/60 backdrop-blur-xs"
            onClick={() => setMobileOpen(false)}
          />
          <aside className="relative z-10 w-72 max-w-[85vw] border-r border-border bg-card p-4">
            {sidebarContent}
          </aside>
        </div>
      )}

      {/* Main Content Area */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-border bg-background/85 px-4 backdrop-blur-md lg:px-8">
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => setMobileOpen(true)}
              className="rounded-md p-1.5 text-muted-foreground hover:bg-accent lg:hidden"
              aria-label="Open navigation"
            >
              <Menu className="h-5 w-5" />
            </button>
            <div className="flex items-center gap-2 text-sm">
              <span className="font-medium text-foreground">
                {activeWorkspace ? activeWorkspace.workspace_name : "Workspace Setup"}
              </span>
              {activeWorkspace && (
                <Badge variant="secondary" className="capitalize">
                  {activeWorkspace.workspace_type}
                </Badge>
              )}
            </div>
          </div>

          <div className="flex items-center gap-2">
            <Badge variant="success" className="hidden sm:inline-flex">
              <ShieldCheck className="mr-1 h-3 w-3" />
              RLS Isolated
            </Badge>
          </div>
        </header>

        <main className="mx-auto w-full max-w-6xl flex-1 p-4 sm:p-6 lg:p-8">{children}</main>
      </div>
    </div>
  );
}
