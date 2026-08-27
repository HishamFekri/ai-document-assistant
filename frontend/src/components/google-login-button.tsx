"use client";


type Props = {
  variant?: "header" | "hero";
};


export default function GoogleLoginButton({
  variant = "hero",
}: Props) {
  const apiUrl =
    process.env.NEXT_PUBLIC_API_URL
    ?? "http://localhost:8000";


  function signIn() {
    window.location.assign(
      `${apiUrl}/auth/google/start`
    );
  }


  const sizeClasses =
    variant === "header"
      ? `
          h-10
          w-[175px]
          px-3
          text-[13px]

          sm:w-auto
          sm:min-w-[180px]
          sm:px-4
          sm:text-sm
        `
      : `
          h-12
          w-[182px]
          px-4
          text-[14px]

          sm:w-auto
          sm:min-w-[220px]
          sm:px-6
          sm:text-[15px]
        `;


  return (
    <button
      type="button"
      onClick={signIn}
      className={`
        inline-flex
        shrink-0
        items-center
        justify-center
        gap-2
        rounded-full

        border
        border-neutral-300
        bg-white
        text-neutral-800

        shadow-sm

        transition-all
        duration-150

        hover:bg-neutral-50
        hover:shadow

        active:scale-[0.98]

        dark:border-neutral-700
        dark:bg-neutral-900
        dark:text-neutral-100
        dark:hover:bg-neutral-800

        ${sizeClasses}
      `}
    >
      <svg
        width="18"
        height="18"
        viewBox="0 0 24 24"
        aria-hidden="true"
        className="shrink-0"
      >
        <path
          fill="#4285F4"
          d="M21.35 12.18c0-.74-.07-1.46-.2-2.14H12v4.05h5.23a4.47 4.47 0 0 1-1.94 2.93v2.43h3.14c1.84-1.69 2.92-4.18 2.92-7.27Z"
        />

        <path
          fill="#34A853"
          d="M12 21.67c2.63 0 4.83-.87 6.44-2.36l-3.14-2.43c-.87.58-1.98.92-3.3.92-2.53 0-4.67-1.71-5.44-4.01H3.32v2.52A9.72 9.72 0 0 0 12 21.67Z"
        />

        <path
          fill="#FBBC05"
          d="M6.56 13.79A5.85 5.85 0 0 1 6.25 12c0-.62.11-1.22.31-1.79V7.69H3.32A9.73 9.73 0 0 0 2.33 12c0 1.56.37 3.04.99 4.31l3.24-2.52Z"
        />

        <path
          fill="#EA4335"
          d="M12 6.2c1.43 0 2.72.49 3.73 1.45l2.79-2.79C16.83 3.29 14.63 2.33 12 2.33A9.72 9.72 0 0 0 3.32 7.69l3.24 2.52C7.33 7.91 9.47 6.2 12 6.2Z"
        />
      </svg>

      <span
        className="
          whitespace-nowrap
          font-medium
        "
      >
        Sign in with Google
      </span>
    </button>
  );
}