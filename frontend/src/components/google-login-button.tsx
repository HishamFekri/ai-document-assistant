"use client";

import { GoogleLogin } from "@react-oauth/google";
import { useRouter } from "next/navigation";


export default function GoogleLoginButton() {
  const router = useRouter();

  const apiUrl =
    process.env.NEXT_PUBLIC_API_URL
    ?? "http://localhost:8000";


  const handleSuccess = async (
    credentialResponse: {
      credential?: string;
    }
  ) => {
    const credential =
      credentialResponse.credential;

    if (!credential) {
      alert(
        "Google did not return a credential."
      );

      return;
    }

    try {
      const response =
        await fetch(
          `${apiUrl}/auth/google`,
          {
            method: "POST",

            credentials: "include",

            headers: {
              "Content-Type":
                "application/json",
            },

            body:
              JSON.stringify({
                credential,
              }),
          }
        );

      const data =
        await response.json();

      if (!response.ok) {
        throw new Error(
          data.detail
          || "Could not sign in"
        );
      }

      router.replace(
        "/chat"
      );

    } catch (error) {
      console.error(
        error
      );

      alert(
        error instanceof Error
          ? error.message
          : "Could not sign in"
      );
    }
  };


  const handleError = () => {
    alert(
      "Google sign-in failed."
    );
  };


  return (
    <GoogleLogin
      onSuccess={
        handleSuccess
      }

      onError={
        handleError
      }

      useOneTap={
        false
      }

      shape="pill"

      size="large"

      text="signin_with"
    />
  );
}