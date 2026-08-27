import type {
  Metadata,
} from "next";

import "./globals.css";

import Providers from "@/components/providers";
import BackendWarmup from "@/components/chat/BackendWarmup";


export const metadata: Metadata = {
  title:
    "AI Document Assistant",

  description:
    "Chat with your documents using AI and get answers grounded in your files.",

  openGraph: {
    title:
      "AI Document Assistant",

    description:
      "Chat with your documents using AI and get answers grounded in your files.",

    type:
      "website",

    images: [
      {
        url:
          "/og-image.jpeg",

        width:
          1200,

        height:
          630,

        alt:
          "AI Document Assistant",
      },
    ],
  },

  twitter: {
    card:
      "summary_large_image",

    title:
      "AI Document Assistant",

    description:
      "Chat with your documents using AI and get answers grounded in your files.",

    images: [
      "/og-image.jpeg",
    ],
  },
};


const themeScript = `
(function () {
  try {
    const savedTheme =
      localStorage.getItem("theme");

    const systemDark =
      window.matchMedia(
        "(prefers-color-scheme: dark)"
      ).matches;

    const shouldUseDark =
      savedTheme === "dark"
      || (
        (
          savedTheme === null
          || savedTheme === "system"
        )
        && systemDark
      );

    document.documentElement.classList.toggle(
      "dark",
      shouldUseDark
    );
  } catch (error) {}
})();
`;


export default function RootLayout({
  children,
}: Readonly<{
  children:
    React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
    >
      <head>
        <link
          rel="preconnect"
          href="https://accounts.google.com"
        />

        <link
          rel="preconnect"
          href="https://accounts.gstatic.com"
          crossOrigin="anonymous"
        />

        <link
          rel="preconnect"
          href="https://www.gstatic.com"
          crossOrigin="anonymous"
        />

        <link
          rel="dns-prefetch"
          href="https://accounts.google.com"
        />

        <link
          rel="dns-prefetch"
          href="https://accounts.gstatic.com"
        />

        <link
          rel="dns-prefetch"
          href="https://www.gstatic.com"
        />

        <script
          dangerouslySetInnerHTML={{
            __html:
              themeScript,
          }}
        />
      </head>

      <body>
        <Providers>
          <BackendWarmup />

          {children}
        </Providers>
      </body>
    </html>
  );
}