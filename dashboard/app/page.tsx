"use client";

import { App } from "@/components/App";
import { useIsClient } from "@/lib/hooks";

export default function Page() {
  // Everything depends on the browser (the URL, stored sign-in), so the
  // prerendered page is empty and the app starts once it is there.
  return useIsClient() ? <App /> : null;
}
