import type { NextConfig } from "next";

// The FastAPI service serves the built files under /dashboard, so the app and
// the API share an origin. `next dev` proxies API calls to a local API instead.
const dev = process.env.NODE_ENV === "development";
const apiOrigin = process.env.API_ORIGIN ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  basePath: "/dashboard",
  trailingSlash: true,
  ...(dev
    ? {
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
