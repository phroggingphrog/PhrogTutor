import { redirect } from "next/navigation";

// Reading and learning activity now lives with the Study list.
export default function LearningProgressPage() {
  redirect("/what-i-know");
}
