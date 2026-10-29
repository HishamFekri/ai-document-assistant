import type { NextConfig } from "next";

import { backendProxyRewrite } from "./src/lib/backend-proxy";
import { standaloneConfig } from "./src/lib/build-output";
import { securityHeaders } from "./src/lib/security-headers";

const nextConfig: NextConfig = {
  reactCompiler: true,
  ...standaloneConfig(process.env),

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