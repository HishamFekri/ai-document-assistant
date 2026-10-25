import type { NextConfig } from "next";

import { backendProxyRewrite } from "./src/lib/backend-proxy";
import { securityHeaders } from "./src/lib/security-headers";

const nextConfig: NextConfig = {
  reactCompiler: true,

  async headers() {
    return [
      {
        source: "/:path*",
        headers: securityHeaders(process.env),
      },
    ];
  },

  async rewrites() {
    return [backendProxyRewrite(process.env)];
  },
};

export default nextConfig;