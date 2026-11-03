import { apiRequest } from "./client";

export interface ServiceStatus {
  status: string;
  environment: string;
}

export function getServiceStatus(signal?: AbortSignal): Promise<ServiceStatus> {
  return apiRequest<ServiceStatus>("/api/status", { signal });
}
