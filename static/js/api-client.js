/**
 * NetAgent API Client
 *
 * Simple fetch wrapper for API calls.
 * Since auth is handled by AWS ALB, we don't need token management.
 */

const apiClient = {
    baseUrl: '',

    /**
     * Make a GET request
     */
    async get(url, params = {}) {
        const queryString = new URLSearchParams(params).toString();
        const fullUrl = queryString ? `${url}?${queryString}` : url;

        const response = await fetch(fullUrl, {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json',
            },
            credentials: 'include',
        });

        if (!response.ok) {
            throw await this._handleError(response);
        }

        return response.json();
    },

    /**
     * Make a POST request
     */
    async post(url, data = {}) {
        const response = await fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            credentials: 'include',
            body: JSON.stringify(data),
        });

        if (!response.ok) {
            throw await this._handleError(response);
        }

        return response.json();
    },

    /**
     * Make a PUT request
     */
    async put(url, data = {}) {
        const response = await fetch(url, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
            },
            credentials: 'include',
            body: JSON.stringify(data),
        });

        if (!response.ok) {
            throw await this._handleError(response);
        }

        return response.json();
    },

    /**
     * Make a DELETE request
     */
    async delete(url) {
        const response = await fetch(url, {
            method: 'DELETE',
            headers: {
                'Content-Type': 'application/json',
            },
            credentials: 'include',
        });

        if (!response.ok) {
            throw await this._handleError(response);
        }

        // Handle 204 No Content
        if (response.status === 204) {
            return null;
        }

        return response.json();
    },

    /**
     * Stream a response (for SSE)
     */
    async *stream(url, data = {}) {
        const response = await fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'text/event-stream',
            },
            credentials: 'include',
            body: JSON.stringify(data),
        });

        if (!response.ok) {
            throw await this._handleError(response);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();

            if (done) break;

            buffer += decoder.decode(value, { stream: true });

            // Process complete SSE events
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    const data = line.slice(6);
                    if (data === '[DONE]') {
                        return;
                    }
                    try {
                        yield JSON.parse(data);
                    } catch (e) {
                        console.warn('Failed to parse SSE data:', data);
                    }
                }
            }
        }
    },

    /**
     * Handle error response
     */
    async _handleError(response) {
        let message = `HTTP ${response.status}`;
        try {
            const data = await response.json();
            message = data.detail || data.message || message;
        } catch (e) {
            // Ignore JSON parse errors
        }

        const error = new Error(message);
        error.status = response.status;
        return error;
    }
};

// Export for modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = apiClient;
}
