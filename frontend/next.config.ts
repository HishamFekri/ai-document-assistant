import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactCompiler: true,

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