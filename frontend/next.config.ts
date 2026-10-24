import type { NextConfig } from "next";

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
    return [
      {
        source: "/api/backend/:path*",
        destination:
          "https://ai-document-assistant-5k8d.onrender.com/:path*",
      },
    ];
  },
};

export default nextConfig;