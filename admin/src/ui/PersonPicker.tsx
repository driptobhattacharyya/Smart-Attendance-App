import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, X } from "lucide-react";
import { api } from "../api/client";
import type { Page, User } from "../api/types";
import { useDebounced, usePerson } from "../lib/queries";
import { Avatar } from "./misc";
import { cx } from "./cx";

/**
 * Search-as-you-type picker for one person (reporting line, desk assignment). The list is
 * server-searched (`GET /people?q=`), so it works for companies of any size.
 */
export function PersonPicker({
  id,
  value,
  onChange,
  excludeId,
  emptyLabel = "Nobody",
  disabled,
}: {
  id?: string;
  value: string | null;
  onChange: (id: string | null) => void;
  /** Hide this person from the results (you cannot report to yourself). */
  excludeId?: string;
  emptyLabel?: string;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [term, setTerm] = useState("");
  const q = useDebounced(term.trim(), 250);
  const selected = usePerson(value);
  const results = useQuery({
    queryKey: ["people", "picker", q],
    enabled: open,
    queryFn: () => api<Page<User>>("/people", { query: { q, limit: 8 } }),
  });
  const options = (results.data?.items ?? []).filter((u) => u.id !== excludeId && u.status !== "disabled");

  return (
    <div className="relative">
      <div className="flex gap-1.5">
        <button
          type="button"
          id={id}
          disabled={disabled}
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
          className={cx(
            "flex h-9 min-w-0 flex-1 items-center gap-2 rounded-lg border border-line-strong bg-surface px-3 text-left text-sm shadow-card transition-colors",
            "hover:border-subtle focus:border-accent focus:ring-2 focus:ring-accent/30 focus:outline-none disabled:cursor-not-allowed disabled:opacity-70",
          )}
        >
          {value ? (
            <>
              <Avatar name={selected.data?.name ?? "?"} size={20} />
              <span className="truncate">{selected.data?.name ?? (selected.isError ? "Person outside your view" : "Loading…")}</span>
            </>
          ) : (
            <span className="truncate text-subtle">{emptyLabel}</span>
          )}
          <ChevronDown className="ml-auto size-4 shrink-0 text-muted" aria-hidden />
        </button>
        {value && !disabled && (
          <button
            type="button"
            aria-label="Clear selection"
            title="Clear"
            onClick={() => onChange(null)}
            className="inline-flex size-9 shrink-0 items-center justify-center rounded-lg text-muted hover:bg-surface-2 hover:text-fg"
          >
            <X className="size-4" />
          </button>
        )}
      </div>
      {open && (
        <div className="mt-1.5 rounded-lg border border-line-strong bg-surface p-2 shadow-card">
          <input
            type="search"
            autoFocus
            aria-label="Search people"
            placeholder="Search by name, email or ID"
            value={term}
            onChange={(e) => setTerm(e.target.value)}
            className="mb-1.5 h-9 w-full rounded-md border border-line-strong bg-surface px-2.5 text-sm focus:border-accent focus:ring-2 focus:ring-accent/30 focus:outline-none"
          />
          <ul role="listbox" aria-label="People" className="scroll-thin max-h-48 overflow-y-auto">
            {results.isLoading && <li className="px-2 py-2 text-xs text-muted">Searching…</li>}
            {!results.isLoading && options.length === 0 && <li className="px-2 py-2 text-xs text-muted">No one found.</li>}
            {options.map((u) => (
              <li key={u.id} role="option" aria-selected={u.id === value}>
                <button
                  type="button"
                  onClick={() => {
                    onChange(u.id);
                    setOpen(false);
                    setTerm("");
                  }}
                  className="flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left hover:bg-surface-2"
                >
                  <Avatar name={u.name} size={24} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm">{u.name}</span>
                    <span className="block truncate text-xs text-muted">
                      {u.employee_code} · {u.email}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
