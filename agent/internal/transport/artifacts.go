// agent/internal/transport/artifacts.go
package transport

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strconv"
)

type ArtifactOpenRequest struct {
	SHA256 string `json:"sha256"`
	Size   int64  `json:"size"`
}

// ArtifactOpenResponse: Status "exists" — сервер уже имеет содержимое,
// "upload" — выдана (или продолжена) сессия загрузки.
type ArtifactOpenResponse struct {
	Status        string `json:"status"`
	UploadID      string `json:"upload_id"`
	ReceivedBytes int64  `json:"received_bytes"`
	ChunkSize     int64  `json:"chunk_size"`
}

// ArtifactChunkResponse: Status "partial" или "complete".
type ArtifactChunkResponse struct {
	ReceivedBytes int64  `json:"received_bytes"`
	Status        string `json:"status"`
}

// OpenArtifact открывает загрузку или узнаёт, что сервер уже имеет файл.
func (c *Client) OpenArtifact(ctx context.Context, sha256 string, size int64) (ArtifactOpenResponse, error) {
	var out ArtifactOpenResponse
	err := c.request(ctx, http.MethodPost, []string{"gateway", "v1", "artifacts"},
		ArtifactOpenRequest{SHA256: sha256, Size: size}, &out)
	return out, err
}

// UploadChunk отправляет чанк. Тело — сырые байты, смещение в заголовке:
// сервер принимает только чанк, начинающийся ровно там, где кончился прошлый.
func (c *Client) UploadChunk(ctx context.Context, uploadID string, offset int64, data []byte) (ArtifactChunkResponse, error) {
	req, err := http.NewRequest(
		http.MethodPut,
		c.base.JoinPath("gateway", "v1", "artifacts", uploadID).String(),
		bytes.NewReader(data),
	)
	if err != nil {
		return ArtifactChunkResponse{}, err
	}
	req.Header.Set("Content-Type", "application/octet-stream")
	req.Header.Set("X-Offset", strconv.FormatInt(offset, 10))

	resp, err := c.do(ctx, req)
	if err != nil {
		return ArtifactChunkResponse{}, err
	}
	defer resp.Body.Close()

	var out ArtifactChunkResponse
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return ArtifactChunkResponse{}, fmt.Errorf("разбор ответа загрузки чанка: %w", err)
	}
	return out, nil
}

// OffsetMismatch извлекает смещение, которое ждёт сервер, из ответа 409.
func OffsetMismatch(err error) (int64, bool) {
	var status *StatusError
	if !errors.As(err, &status) || status.Code != http.StatusConflict {
		return 0, false
	}
	var body struct {
		ReceivedBytes int64 `json:"received_bytes"`
	}
	if json.Unmarshal([]byte(status.Body), &body) != nil {
		return 0, false
	}
	return body.ReceivedBytes, true
}
