"use client";

import {
  ArrowRight,
  FileSpreadsheet,
  FileText,
  MessageSquareText,
  Search,
  ShieldCheck,
  Sparkles,
} from "lucide-react";

import GoogleLoginButton from "@/components/google-login-button";


export default function Home() {
  return (
    <main
      className="
        relative
        min-h-screen
        overflow-hidden
        bg-[var(--background)]
        text-[var(--text-primary)]
        transition-colors
        duration-200
      "
    >
      <LandingAmbient />

      <div className="relative z-10">
        <Navbar />
        <Hero />
        <ProductPreview />
        <Features />
        <HowItWorks />
        <FinalCTA />
        <Footer />
      </div>
    </main>
  );
}


function LandingAmbient() {
  return (
    <div
      className="landing-ambient"
      aria-hidden="true"
    >
      <div className="ambient-orb ambient-orb-one" />
      <div className="ambient-orb ambient-orb-two" />
      <div className="ambient-orb ambient-orb-three" />
      <div className="ambient-orb ambient-orb-four" />
      <div className="ambient-orb ambient-orb-five" />
      <div className="ambient-orb ambient-orb-six" />
      <div className="ambient-orb ambient-orb-seven" />
      <div className="ambient-orb ambient-orb-eight" />

      <div className="ambient-grid" />
      <div className="ambient-noise" />
    </div>
  );
}


function Navbar() {
  return (
    <nav
      className="
        mx-auto
        flex
        max-w-7xl
        items-center
        justify-between
        px-6
        py-6
        lg:px-8
      "
    >
      <div className="flex items-center gap-2.5">
        <div
          className="
            flex
            h-8
            w-8
            items-center
            justify-center
            rounded-[10px]
            border
            border-[var(--border)]
            bg-[var(--surface)]
            text-[var(--primary)]
            shadow-sm
          "
        >
          <Sparkles
            size={15}
            strokeWidth={2}
          />
        </div>

        <span
          className="
            text-[15px]
            font-semibold
            tracking-[-0.02em]
          "
        >
          AI Document Assistant
        </span>
      </div>


      <div
        className="
          hidden
          items-center
          gap-8
          text-[13px]
          text-[var(--text-secondary)]
          md:flex
        "
      >
        <a
          href="#features"
          className="
            transition-colors
            hover:text-[var(--primary)]
          "
        >
          Features
        </a>

        <a
          href="#how-it-works"
          className="
            transition-colors
            hover:text-[var(--primary)]
          "
        >
          How it works
        </a>
      </div>


      <GoogleLoginButton />
    </nav>
  );
}


function Hero() {
  return (
    <section
      className="
        mx-auto
        max-w-7xl
        px-6
        pb-10
        pt-24
        lg:px-8
        lg:pt-32
      "
    >
      <div
        className="
          mx-auto
          max-w-4xl
          text-center
        "
      >
        <div
          className="
            mb-7
            inline-flex
            items-center
            gap-2
            rounded-full
            border
            border-[var(--border)]
            bg-[var(--surface)]
            px-3.5
            py-1.5
            text-[12px]
            font-medium
            text-[var(--text-secondary)]
            shadow-sm
            backdrop-blur-xl
          "
        >
          <span
            className="
              h-1.5
              w-1.5
              rounded-full
              bg-[var(--primary)]
              shadow-[0_0_12px_rgba(102,117,232,0.7)]
            "
          />

          AI that understands your files
        </div>


        <h1
          className="
            text-[48px]
            font-semibold
            leading-[1.02]
            tracking-[-0.055em]
            sm:text-[64px]
            lg:text-[78px]
          "
        >
          <span className="hero-title-main">
            Your documents,
          </span>

          <br />

          <span className="hero-title-accent">
            Finally make sense.
          </span>
        </h1>


        <p
          className="
            mx-auto
            mt-7
            max-w-2xl
            text-[17px]
            leading-7
            text-[var(--text-secondary)]
            sm:text-[18px]
          "
        >
          Ask questions across PDFs, Word files,
          spreadsheets, and notes. Get clear answers
          grounded in the content you actually uploaded.
        </p>


        <div
          className="
            mt-9
            flex
            flex-col
            items-center
            justify-center
            gap-3
            sm:flex-row
          "
        >
          <a
            href="#how-it-works"
            className="
              group
              flex
              h-12
              items-center
              justify-center
              gap-2
              rounded-full
              bg-[var(--text-primary)]
              px-6
              text-sm
              font-medium
              text-[var(--background)]
              transition-all
              duration-200
              hover:opacity-85
              active:scale-[0.98]
            "
          >
            See how it works

            <ArrowRight
              size={16}
              className="
                transition-transform
                duration-200
                group-hover:translate-x-0.5
              "
            />
          </a>


          <div
            className="
              flex
              h-12
              items-center
              px-1
            "
          >
            <GoogleLoginButton />
          </div>
        </div>


        <div
          className="
            mt-8
            flex
            flex-wrap
            items-center
            justify-center
            gap-2
            text-[11px]
            text-[var(--text-muted)]
          "
        >
          <FileBadge label="PDF" />
          <FileBadge label="DOCX" />
          <FileBadge label="XLSX" />
          <FileBadge label="TXT" />
        </div>
      </div>
    </section>
  );
}


function FileBadge({
  label,
}: {
  label: string;
}) {
  return (
    <span
      className="
        rounded-md
        border
        border-[var(--border)]
        bg-[var(--surface)]
        px-2
        py-1
        backdrop-blur-xl
      "
    >
      {label}
    </span>
  );
}


function ProductPreview() {
  return (
    <section
      className="
        mx-auto
        max-w-6xl
        px-4
        pb-28
        pt-16
        sm:px-6
        lg:px-8
      "
    >
      <div
        className="
          relative
          mx-auto
          max-w-5xl
        "
      >
        <div
          className="
            overflow-hidden
            rounded-[28px]
            border
            border-[var(--border)]
            bg-[var(--surface)]
            shadow-[0_30px_100px_rgba(0,0,0,0.28)]
            backdrop-blur-xl
          "
        >
          <div
            className="
              relative
              flex
              h-12
              items-center
              border-b
              border-[var(--border)]
              px-5
            "
          >
            <div className="flex gap-1.5">
              <span className="h-2.5 w-2.5 rounded-full bg-[var(--border)]" />
              <span className="h-2.5 w-2.5 rounded-full bg-[var(--border)]" />
              <span className="h-2.5 w-2.5 rounded-full bg-[var(--border)]" />
            </div>

            <span
              className="
                absolute
                left-1/2
                -translate-x-1/2
                text-[11px]
                text-[var(--text-muted)]
              "
            >
              AI Document Assistant
            </span>
          </div>


          <div
            className="
              grid
              min-h-[540px]
              md:grid-cols-[220px_1fr]
            "
          >
            <div
              className="
                hidden
                border-r
                border-[var(--border)]
                bg-[var(--background)]
                p-4
                md:block
              "
            >
              <div
                className="
                  flex
                  items-center
                  gap-2
                  rounded-xl
                  px-2
                  py-2
                  text-[12px]
                  font-medium
                "
              >
                <Sparkles
                  size={14}
                  className="
                    text-[var(--primary)]
                  "
                />

                New chat
              </div>


              <div
                className="
                  mt-6
                  px-2
                  text-[10px]
                  font-medium
                  uppercase
                  tracking-[0.08em]
                  text-[var(--text-muted)]
                "
              >
                Recent
              </div>


              <div className="mt-2 space-y-1">
                <DemoChatRow
                  active
                  text="Technical manual"
                />

                <DemoChatRow
                  text="Project requirements"
                />

                <DemoChatRow
                  text="Research notes"
                />
              </div>
            </div>


            <div
              className="
                flex
                min-w-0
                flex-col
                bg-[var(--background)]
              "
            >
              <div
                className="
                  flex
                  h-14
                  items-center
                  justify-between
                  border-b
                  border-[var(--border)]
                  px-5
                "
              >
                <p
                  className="
                    truncate
                    text-[13px]
                    font-medium
                  "
                >
                  Technical manual
                </p>


                <div
                  className="
                    flex
                    items-center
                    gap-2
                    text-[11px]
                    text-[var(--text-muted)]
                  "
                >
                  <FileText size={14} />

                  Documents
                </div>
              </div>


              <div
                className="
                  flex
                  flex-1
                  flex-col
                  justify-between
                  p-5
                  sm:p-8
                "
              >
                <div
                  className="
                    mx-auto
                    w-full
                    max-w-2xl
                    space-y-8
                  "
                >
                  <div className="flex justify-end">
                    <div
                      className="
                        max-w-[75%]
                        rounded-[20px]
                        rounded-br-[6px]
                        bg-[var(--surface-active)]
                        px-4
                        py-3
                        text-[13px]
                        leading-6
                        text-[var(--text-primary)]
                      "
                    >
                      What do labels 1 and 2 identify in
                      the diagram?
                    </div>
                  </div>


                  <div className="flex gap-3">
                    <div
                      className="
                        mt-0.5
                        flex
                        h-7
                        w-7
                        shrink-0
                        items-center
                        justify-center
                        rounded-full
                        border
                        border-[var(--border)]
                        bg-[var(--surface)]
                        text-[var(--primary)]
                      "
                    >
                      <Sparkles size={13} />
                    </div>


                    <div className="max-w-xl">
                      <p
                        className="
                          text-[13px]
                          leading-6
                          text-[var(--text-primary)]
                        "
                      >
                        Labels 1 and 2 identify the vacuum
                        cells. Label 1 refers to the vacuum
                        cell for the large flap, while label
                        2 identifies the vacuum cell for the
                        small flap.
                      </p>


                      <button
                        type="button"
                        className="
                          mt-4
                          flex
                          items-center
                          gap-2
                          rounded-lg
                          border
                          border-[var(--border)]
                          bg-[var(--surface)]
                          px-2.5
                          py-1.5
                          text-[10px]
                          text-[var(--text-secondary)]
                          transition
                          hover:bg-[var(--surface-hover)]
                          hover:text-[var(--primary)]
                        "
                      >
                        <FileText
                          size={12}
                          className="
                            text-[var(--primary)]
                          "
                        />

                        Technical Manual · Page 24
                      </button>
                    </div>
                  </div>
                </div>


                <div
                  className="
                    mx-auto
                    mt-12
                    w-full
                    max-w-2xl
                  "
                >
                  <div
                    className="
                      flex
                      min-h-[56px]
                      items-center
                      gap-3
                      rounded-[28px]
                      border
                      border-[var(--border)]
                      bg-[var(--surface)]
                      px-4
                      shadow-sm
                    "
                  >
                    <span
                      className="
                        text-xl
                        font-light
                        text-[var(--text-muted)]
                      "
                    >
                      +
                    </span>

                    <span
                      className="
                        flex-1
                        text-[12px]
                        text-[var(--text-muted)]
                      "
                    >
                      Ask anything
                    </span>

                    <div
                      className="
                        flex
                        h-8
                        w-8
                        items-center
                        justify-center
                        rounded-full
                        bg-[var(--primary)]
                        text-white
                      "
                    >
                      <ArrowRight size={14} />
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>


        <p
          className="
            mt-5
            text-center
            text-[11px]
            text-[var(--text-muted)]
          "
        >
          A focused workspace for understanding your
          documents.
        </p>
      </div>
    </section>
  );
}


function DemoChatRow({
  text,
  active = false,
}: {
  text: string;
  active?: boolean;
}) {
  return (
    <div
      className={`
        truncate
        rounded-lg
        px-2
        py-2
        text-[11px]

        ${
          active
            ? "bg-[var(--surface-active)] text-[var(--text-primary)]"
            : "text-[var(--text-secondary)]"
        }
      `}
    >
      {text}
    </div>
  );
}


function Features() {
  return (
    <section
      id="features"
      className="
        border-y
        border-[var(--border)]
      "
    >
      <div
        className="
          mx-auto
          max-w-6xl
          px-6
          py-28
          lg:px-8
        "
      >
        <div
          className="
            grid
            gap-12
            lg:grid-cols-[0.8fr_1.2fr]
            lg:gap-20
          "
        >
          <div>
            <span
              className="
                text-[11px]
                font-semibold
                uppercase
                tracking-[0.12em]
                text-[var(--primary)]
              "
            >
              Built for clarity
            </span>


            <h2
              className="
                mt-4
                max-w-md
                text-3xl
                font-semibold
                leading-tight
                tracking-[-0.035em]
                sm:text-4xl
              "
            >
              Less searching.
              <br />
              More understanding.
            </h2>


            <p
              className="
                mt-5
                max-w-md
                text-[15px]
                leading-7
                text-[var(--text-secondary)]
              "
            >
              Your files become one searchable knowledge
              space, so you can spend less time finding
              information and more time using it.
            </p>
          </div>


          <div
            className="
              grid
              gap-px
              overflow-hidden
              rounded-2xl
              border
              border-[var(--border)]
              bg-[var(--border)]
              sm:grid-cols-2
            "
          >
            <Feature
              icon={<Search size={18} />}
              title="Grounded answers"
              description="Answers are based on your uploaded documents, with relevant context retrieved automatically."
            />

            <Feature
              icon={<FileSpreadsheet size={18} />}
              title="One workspace"
              description="Work across PDFs, Word documents, spreadsheets, and text files together."
            />

            <Feature
              icon={<ShieldCheck size={18} />}
              title="Trace the source"
              description="See the document and location behind an answer instead of trusting a black box."
            />

            <Feature
              icon={<MessageSquareText size={18} />}
              title="Natural conversation"
              description="Ask follow-up questions naturally without repeatedly searching through files."
            />
          </div>
        </div>
      </div>
    </section>
  );
}


function Feature({
  icon,
  title,
  description,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
}) {
  return (
    <div
      className="
        bg-[var(--background)]
        p-7
        transition-colors
        duration-200
        hover:bg-[var(--surface)]
      "
    >
      <div
        className="
          mb-8
          flex
          h-9
          w-9
          items-center
          justify-center
          rounded-lg
          border
          border-[var(--border)]
          text-[var(--primary)]
        "
      >
        {icon}
      </div>

      <h3
        className="
          text-[14px]
          font-semibold
        "
      >
        {title}
      </h3>

      <p
        className="
          mt-2
          text-[13px]
          leading-6
          text-[var(--text-secondary)]
        "
      >
        {description}
      </p>
    </div>
  );
}


function HowItWorks() {
  return (
    <section
      id="how-it-works"
      className="
        mx-auto
        max-w-6xl
        px-6
        py-28
        lg:px-8
      "
    >
      <div
        className="
          mx-auto
          max-w-2xl
          text-center
        "
      >
        <span
          className="
            text-[11px]
            font-semibold
            uppercase
            tracking-[0.12em]
            text-[var(--primary)]
          "
        >
          Simple by design
        </span>

        <h2
          className="
            mt-4
            text-3xl
            font-semibold
            tracking-[-0.035em]
            sm:text-4xl
          "
        >
          From file to answer.
        </h2>

        <p
          className="
            mt-4
            text-[15px]
            leading-7
            text-[var(--text-secondary)]
          "
        >
          No complicated setup. Add your documents and
          start asking questions.
        </p>
      </div>


      <div
        className="
          relative
          mx-auto
          mt-16
          grid
          max-w-4xl
          gap-4
          md:grid-cols-3
        "
      >
        <Step
          number="01"
          title="Add your files"
          description="Upload the documents you want to work with."
        />

        <Step
          number="02"
          title="We prepare them"
          description="The system extracts, organizes, and indexes their content."
        />

        <Step
          number="03"
          title="Start asking"
          description="Ask naturally and get answers grounded in those files."
        />
      </div>
    </section>
  );
}


function Step({
  number,
  title,
  description,
}: {
  number: string;
  title: string;
  description: string;
}) {
  return (
    <div
      className="
        rounded-2xl
        border
        border-[var(--border)]
        bg-[var(--surface)]
        p-6
      "
    >
      <div
        className="
          mb-12
          flex
          items-center
          justify-between
        "
      >
        <span
          className="
            text-[11px]
            font-medium
            text-[var(--text-muted)]
          "
        >
          {number}
        </span>

        <span
          className="
            h-1.5
            w-1.5
            rounded-full
            bg-[var(--primary)]
          "
        />
      </div>

      <h3
        className="
          text-[14px]
          font-semibold
        "
      >
        {title}
      </h3>

      <p
        className="
          mt-2
          text-[13px]
          leading-6
          text-[var(--text-secondary)]
        "
      >
        {description}
      </p>
    </div>
  );
}


function FinalCTA() {
  return (
    <section
      className="
        px-6
        pb-28
        pt-8
        lg:px-8
      "
    >
      <div
        className="
          relative
          mx-auto
          max-w-5xl
          overflow-hidden
          rounded-[32px]
          border
          border-[var(--border)]
          bg-[var(--surface)]
          px-6
          py-20
          text-center
          shadow-2xl
          shadow-black/10
          sm:px-12
        "
      >
        <div
          className="
            pointer-events-none
            absolute
            left-1/2
            top-0
            h-52
            w-[520px]
            -translate-x-1/2
            -translate-y-1/2
            rounded-full
            bg-[rgba(65,105,225,0.22)]
            blur-[100px]
          "
        />

        <Sparkles
          size={20}
          className="
            mx-auto
            text-[var(--primary)]
          "
        />

        <h2
          className="
            mx-auto
            mt-6
            max-w-xl
            text-3xl
            font-semibold
            tracking-[-0.035em]
            sm:text-4xl
          "
        >
          Stop digging through documents.
        </h2>

        <p
          className="
            mx-auto
            mt-4
            max-w-lg
            text-[14px]
            leading-6
            text-[var(--text-secondary)]
          "
        >
          Turn your files into a workspace you can
          actually have a conversation with.
        </p>

        <div
          className="
            mt-8
            flex
            justify-center
          "
        >
          <GoogleLoginButton />
        </div>
      </div>
    </section>
  );
}


function Footer() {
  return (
    <footer
      className="
        border-t
        border-[var(--border)]
      "
    >
      <div
        className="
          mx-auto
          flex
          max-w-7xl
          flex-col
          gap-3
          px-6
          py-8
          text-[12px]
          text-[var(--text-muted)]
          sm:flex-row
          sm:items-center
          sm:justify-between
          lg:px-8
        "
      >
        <span>
          AI Document Assistant
        </span>

        <span>
          Your documents. Your answers.
        </span>
      </div>
    </footer>
  );
}