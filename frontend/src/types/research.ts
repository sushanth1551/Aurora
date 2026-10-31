export interface ResearchRequest {
  query: string;
  max_results?: number;
  scrape_pages?: boolean;
  country?: string;
  language?: string;
}

export type JobStatus =
  | "pending"
  | "running"
  | "completed"
  | "partially_completed"
  | "failed";

export interface ErrorDetail {
  stage: string | null;
  category: string;
  message: string;
}

export interface ResearchSource {
  id: string;
  original_url: string;
  normalized_url: string | null;
  final_url: string | null;
  domain: string | null;
  title: string | null;
  snippet: string | null;
  extracted_markdown: string | null;
  extraction_status: "pending" | "succeeded" | "failed" | "rejected" | "skipped";
  content_hash: string | null;
  error_message: string | null;
}

export interface ResearchResponse {
  job_id: string;
  query: string;
  status: JobStatus;
  actual_result_count: number;
  successful_source_count: number;
  failed_source_count: number;
  sources: ResearchSource[];
  errors: ErrorDetail[];
  failure: ErrorDetail | null;
}
