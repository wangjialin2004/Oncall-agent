import { getSessionOwnerToken } from "./agentStream";

type UploadedFileMetadata = {
  file_id: string;
  original_name: string;
};

type UploadFileEnvelope = {
  code: number;
  message: string;
  data?: {
    file?: UploadedFileMetadata;
    deduplicated?: boolean;
  };
};

export type UploadFileResult = {
  fileId: string;
  fileName: string;
  deduplicated: boolean;
};

export async function uploadFile(file: File): Promise<UploadFileResult> {
  const authToken = localStorage.getItem("authToken");
  const headers: Record<string, string> = {
    "X-Session-Owner": getSessionOwnerToken(),
  };
  if (authToken) {
    headers["Authorization"] = `Bearer ${authToken}`;
  }

  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch("/api/files", {
    method: "POST",
    headers,
    body: formData,
  });

  const json = (await response.json().catch(() => null)) as UploadFileEnvelope | null;

  if (!response.ok || !json) {
    throw new Error(json?.message || `HTTP ${response.status}`);
  }

  if (json.code !== 200 || !json.data?.file?.original_name) {
    throw new Error(json.message || "文件上传失败");
  }

  return {
    fileId: json.data.file.file_id,
    fileName: json.data.file.original_name,
    deduplicated: Boolean(json.data.deduplicated),
  };
}
