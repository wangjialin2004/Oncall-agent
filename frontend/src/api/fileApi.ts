import { apiFetch } from "./httpClient";

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
  const formData = new FormData();
  formData.append("file", file);

  const response = await apiFetch(
    "/api/files",
    {
      method: "POST",
      body: formData,
    },
    { json: false, errorMessage: "文件上传失败" },
  );

  const json = (await response.json().catch(() => null)) as UploadFileEnvelope | null;

  if (!json) {
    throw new Error("文件上传失败");
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
