import type { NextConfig } from "next";

// The FastAPI service serves the built files under /dashboard, so the app and
// the API share an origin. `next dev` proxies API calls to a local API instead.
const dev = process.env.NODE_ENV === "development";
const apiOrigin = process.env.API_ORIGIN ?? "http://127.0.0.1:8000";
// Hosts other than localhost that may open `next dev`, such as this machine's
// LAN address for a tablet: DEV_ORIGINS=192.168.1.20,other-host
const devOrigins = (process.env.DEV_ORIGINS ?? "")
  .split(",")
  .map((host) => host.trim())
  .filter(Boolean);

const nextConfig: NextConfig = {
  basePath: "/dashboard",
  trailingSlash: true,
  ...(dev
    ? {
        // Otherwise trailingSlash 308s API calls to `/…/login/` before the
        // rewrite below forwards them, and FastAPI has no such routes.
        skipTrailingSlashRedirect: true,
        allowedDevOrigins: devOrigins,
        async rewrites() {
          return [
            {
              source: "/restaurants/:path*",
              destination: `${apiOrigin}/restaurants/:path*`,
              basePath: false as const,
            },
          ];
        },
      }
    : { output: "export" as const }),
};

export default nextConfig;
