import type { Metadata } from "next";

import { SignupForm } from "@/components/auth/SignupForm";

export const metadata: Metadata = { title: "Create your account" };

export default function SignupPage() {
  return <SignupForm />;
}
