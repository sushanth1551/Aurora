export interface ResearchRequest {
  topic: string;
}

export interface ResearchAcknowledgement {
  request_id: string;
  topic: string;
  status: "not_implemented";
  message: string;
  is_placeholder: boolean;
  created_at: string;
}
