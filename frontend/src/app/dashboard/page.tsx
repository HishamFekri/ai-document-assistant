"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import {
  FileText,
  LogOut,
  Plus,
  Sparkles,
  Upload,
} from "lucide-react";


type User = {
  id: number;
  email: string;
  name: string | null;
  picture: string | null;
  created_at: string;
};


type Document = {
  id: number;
  filename: string;
  file_type: string | null;
  pages_count: number | null;

  processing_status: string;
  processing_stage: string | null;
  processing_progress: number;

  created_at: string;
};


type Chat = {
  id: number;
  title: string | null;
  created_at: string;
};


export default function DashboardPage() {
  const router = useRouter();

  const [user, setUser] = useState<User | null>(
    null
  );

  const [documents, setDocuments] = useState<
    Document[]
  >([]);

  const [chats, setChats] = useState<
    Chat[]
  >([]);

  const [loading, setLoading] = useState(
    true
  );


  async function loadDashboard() {
    const token = "";

    try {
      const headers = {};

      const [
        userResponse,
        documentsResponse,
        chatsResponse,
      ] = await Promise.all([
        fetch(
          `${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/auth/me`,
          {
            credentials: "include",
            headers,
          }
        ),

        fetch(
          `${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/documents`,
          {
            credentials: "include",
            headers,
          }
        ),

        fetch(
          `${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/chats`,
          {
            credentials: "include",
            headers,
          }
        ),
      ]);

      if (
        userResponse.status === 401
      ) {
        router.push("/");
        return;
      }

      if (!userResponse.ok) {
        throw new Error(
          "Could not load user"
        );
      }

      if (!documentsResponse.ok) {
        throw new Error(
          "Could not load documents"
        );
      }

      if (!chatsResponse.ok) {
        throw new Error(
          "Could not load chats"
        );
      }

      const userData =
        await userResponse.json();

      const documentsData =
        await documentsResponse.json();

      const chatsData =
        await chatsResponse.json();

      setUser(
        userData
      );

      setDocuments(
        documentsData
      );

      setChats(
        chatsData
      );
    } catch (error) {
      console.error(
        error
      );
    } finally {
      setLoading(
        false
      );
    }
  }


  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void loadDashboard();
    }, 0);

    return () => window.clearTimeout(timeoutId);
  }, []);


  function logout() {
    fetch(
      `${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/auth/logout`,
      {
        method: "POST",
        credentials: "include",
      }
    ).catch((error) => {
      console.error("[LOGOUT ERROR]", error);
    });

    router.push("/");
  }


  if (loading) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-neutral-50">
        <div className="flex items-center gap-3 text-sm text-neutral-500">
          <Sparkles
            size={18}
            className="animate-pulse"
          />

          Loading your workspace...
        </div>
      </main>
    );
  }


  return (
    <main className="min-h-screen bg-neutral-50 text-neutral-950">
      <header className="border-b border-neutral-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4 lg:px-8">
          <div className="flex items-center gap-2">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-black text-white">
              <Sparkles
                size={18}
              />
            </div>

            <span className="font-semibold tracking-tight">
              AI Document Assistant
            </span>
          </div>

          <div className="flex items-center gap-4">
            {user?.picture && (
              <img
                src={user.picture}
                alt={
                  user.name ||
                  "Profile"
                }
                className="h-9 w-9 rounded-full"
              />
            )}

            <div className="hidden text-right sm:block">
              <p className="text-sm font-medium">
                {user?.name || "User"}
              </p>

              <p className="text-xs text-neutral-400">
                {user?.email}
              </p>
            </div>

            <button
              onClick={logout}
              className="flex h-9 w-9 items-center justify-center rounded-full border border-neutral-200 text-neutral-500 transition hover:bg-neutral-50 hover:text-black"
            >
              <LogOut
                size={16}
              />
            </button>
          </div>
        </div>
      </header>


      <section className="mx-auto max-w-7xl px-6 py-10 lg:px-8">
        <div className="mb-10 flex flex-col justify-between gap-5 sm:flex-row sm:items-end">
          <div>
            <p className="text-sm text-neutral-500">
              Workspace
            </p>

            <h1 className="mt-1 text-3xl font-semibold tracking-tight">
              Welcome back
              {user?.name
                ? `, ${user.name.split(" ")[0]}`
                : ""}
              .
            </h1>

            <p className="mt-2 text-neutral-500">
              Upload documents and start asking questions.
            </p>
          </div>

          <button className="flex items-center justify-center gap-2 rounded-xl bg-black px-5 py-3 text-sm font-medium text-white transition hover:bg-neutral-800">
            <Plus
              size={17}
            />

            New chat
          </button>
        </div>


        <div className="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
          <section className="rounded-2xl border border-neutral-200 bg-white p-6">
            <div className="mb-5">
              <h2 className="font-semibold">
                Upload documents
              </h2>

              <p className="mt-1 text-sm text-neutral-500">
                PDF, DOCX, XLSX, or TXT.
                Maximum file size 50 MB.
              </p>
            </div>

            <button className="flex min-h-52 w-full flex-col items-center justify-center rounded-2xl border border-dashed border-neutral-300 bg-neutral-50 transition hover:border-neutral-400 hover:bg-neutral-100">
              <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-white shadow-sm">
                <Upload
                  size={21}
                />
              </div>

              <span className="text-sm font-medium">
                Upload a document
              </span>

              <span className="mt-1 text-xs text-neutral-400">
                Click to choose a file
              </span>
            </button>
          </section>


          <section className="rounded-2xl border border-neutral-200 bg-white p-6">
            <div className="mb-5 flex items-center justify-between">
              <div>
                <h2 className="font-semibold">
                  Recent chats
                </h2>

                <p className="mt-1 text-sm text-neutral-500">
                  Continue where you left off.
                </p>
              </div>
            </div>

            <div className="space-y-2">
              {chats.length === 0 && (
                <div className="rounded-xl bg-neutral-50 px-4 py-5 text-sm text-neutral-400">
                  No chats yet.
                </div>
              )}

              {chats
                .slice(0, 5)
                .map((chat) => (
                  <button
                    key={chat.id}
                    onClick={() =>
                      router.push(
                        `/chat/${chat.id}`
                      )
                    }
                    className="flex w-full items-center justify-between rounded-xl px-4 py-3 text-left transition hover:bg-neutral-50"
                  >
                    <div>
                      <p className="text-sm font-medium">
                        {chat.title ||
                          "Untitled chat"}
                      </p>

                      <p className="mt-1 text-xs text-neutral-400">
                        {new Date(
                          chat.created_at
                        ).toLocaleDateString()}
                      </p>
                    </div>

                    <span className="text-neutral-300">
                      →
                    </span>
                  </button>
                ))}
            </div>
          </section>
        </div>


        <section className="mt-6 rounded-2xl border border-neutral-200 bg-white p-6">
          <div className="mb-5 flex items-center justify-between">
            <div>
              <h2 className="font-semibold">
                Your documents
              </h2>

              <p className="mt-1 text-sm text-neutral-500">
                Documents available in your workspace.
              </p>
            </div>

            <span className="text-sm text-neutral-400">
              {documents.length}
            </span>
          </div>

          <div className="divide-y divide-neutral-100">
            {documents.length === 0 && (
              <div className="py-10 text-center text-sm text-neutral-400">
                No documents uploaded yet.
              </div>
            )}

            {documents.map(
              (document) => (
                <div
                  key={document.id}
                  className="flex flex-col gap-4 py-4 sm:flex-row sm:items-center sm:justify-between"
                >
                  <div className="flex min-w-0 items-center gap-3">
                    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-neutral-100">
                      <FileText
                        size={18}
                      />
                    </div>

                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">
                        {
                          document.filename
                        }
                      </p>

                      <p className="mt-1 text-xs text-neutral-400">
                        {document.file_type?.toUpperCase()}

                        {document.pages_count
                          ? ` · ${document.pages_count} pages`
                          : ""}
                      </p>
                    </div>
                  </div>


                  <DocumentStatus
                    document={
                      document
                    }
                  />
                </div>
              )
            )}
          </div>
        </section>
      </section>
    </main>
  );
}


function DocumentStatus({
  document,
}: {
  document: Document;
}) {
  if (
    document.processing_status ===
    "ready"
  ) {
    return (
      <div className="text-sm font-medium text-emerald-600">
        Ready
      </div>
    );
  }


  if (
    document.processing_status ===
    "failed"
  ) {
    return (
      <div className="text-sm font-medium text-red-500">
        Failed
      </div>
    );
  }


  return (
    <div className="w-full max-w-48">
      <div className="mb-1 flex items-center justify-between text-xs text-neutral-400">
        <span>
          Processing
        </span>

        <span>
          {document.processing_progress}%
        </span>
      </div>

      <div className="h-1.5 overflow-hidden rounded-full bg-neutral-100">
        <div
          className="h-full rounded-full bg-black transition-all duration-500"
          style={{
            width: `${document.processing_progress}%`,
          }}
        />
      </div>
    </div>
  );
}