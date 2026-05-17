# Understanding the Frontend Audio Capture Logic

The audio capture logic implemented in Iteration 3 utilizes modern Web APIs to capture raw microphone data, process it efficiently without blocking the browser, and stream it to the backend via WebSockets.

This document breaks down the implementation details and explains how to adapt this logic for a modern React application.

## 1. The Core Technologies

We use three primary Web APIs to achieve real-time audio streaming:

1.  **WebRTC (`navigator.mediaDevices.getUserMedia`)**: This prompts the user for microphone permissions and provides a raw `MediaStream`.
2.  **Web Audio API (`AudioContext`)**: This is the processing engine. It takes the raw stream and allows us to manipulate or analyze the audio data.
3.  **AudioWorklet (`AudioWorkletNode` & `AudioWorkletProcessor`)**: This is the most crucial part. Older methods (like `ScriptProcessorNode`) ran on the main browser thread, causing UI lag and audio stuttering. **AudioWorklets run on a dedicated background audio thread.**

## 2. Step-by-Step Breakdown

### Step 1: Initializing the AudioContext
When the user clicks "Start Audio", we first request microphone access:
```javascript
const mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
```
Next, we create the `AudioContext`. Crucially, we force the `sampleRate` to `16000` (16kHz). 
```javascript
audioContext = new AudioContext({ sampleRate: 16000 }); 
```
*Why 16kHz?* OpenAI's Whisper model (which we will use in Iteration 4 for transcription) is natively trained on 16kHz audio. By forcing the browser to capture at 16kHz, we save the backend from having to perform expensive audio resampling later.

### Step 2: The AudioWorkletProcessor
We define a processor script. Because Worklets run in a separate thread, they cannot directly access variables in the main script. They must be loaded as a separate file or inline blob.

```javascript
class AudioCaptureProcessor extends AudioWorkletProcessor {
    process(inputs, outputs, parameters) {
        const input = inputs[0];
        if (input && input.length > 0) {
            const channelData = input[0]; // Get the raw Float32Array data
            this.port.postMessage(channelData); // Send it back to the main thread
        }
        return true;
    }
}
```
This processor simply grabs every chunk of audio the microphone hears (usually 128 samples at a time) and posts it back to the main thread.

### Step 3: Buffering the Audio
Back on the main thread, we listen to messages from the Worklet. We accumulate the tiny `Float32Array` chunks into a larger buffer.

```javascript
captureNode.port.onmessage = (event) => {
    const data = event.data; 
    audioBuffer.push(...data);
    
    // SAMPLES_PER_CHUNK = 16000 (Hz) * 3 (seconds) = 48000 samples
    if (audioBuffer.length >= SAMPLES_PER_CHUNK) {
        sendAudioChunk(new Float32Array(audioBuffer));
        audioBuffer = []; // Reset buffer for the next 3 seconds
    }
};
```
We wait until we have exactly 3 seconds of audio before sending it. Sending audio too frequently overwhelms the WebSocket, while sending it too infrequently creates a massive delay in live transcriptions. 3 seconds is the "sweet spot" for live AI transcription.

### Step 4: Format Conversion & Transmission
The Web Audio API natively works with `Float32Array` (values between -1.0 and 1.0). However, most AI models and standard audio formats (like `.wav`) expect 16-bit PCM (Pulse-Code Modulation), which uses integers.

Before sending over Socket.IO, we map the floats to 16-bit integers (Int16):
```javascript
const int16Array = new Int16Array(float32Array.length);
for (let i = 0; i < float32Array.length; i++) {
    let s = Math.max(-1, Math.min(1, float32Array[i]));
    int16Array[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
}
```
Finally, we emit the binary `.buffer` over Socket.IO.

---

## 3. How to use this in a React App

When you transition to React (e.g., Next.js or Vite), the logic remains the same, but it must be managed within React's lifecycle hooks (`useEffect`, `useRef`).

Here is a conceptual example of a custom React hook `useAudioCapture`:

### `public/audio-processor.js`
In React, you shouldn't use inline Blob strings for Worklets. Instead, place the processor in your `public/` folder so it can be fetched reliably.
```javascript
// public/audio-processor.js
class AudioCaptureProcessor extends AudioWorkletProcessor {
    process(inputs, outputs, parameters) {
        const input = inputs[0];
        if (input && input.length > 0) {
            this.port.postMessage(input[0]);
        }
        return true;
    }
}
registerProcessor('audio-capture-processor', AudioCaptureProcessor);
```

### `hooks/useAudioCapture.js`
```javascript
import { useRef, useState, useCallback } from 'react';

export const useAudioCapture = (socket, room, userName) => {
    const [isRecording, setIsRecording] = useState(false);
    const audioContextRef = useRef(null);
    const streamRef = useRef(null);
    const bufferRef = useRef([]);

    const startRecording = useCallback(async () => {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            streamRef.current = stream;

            const context = new (window.AudioContext || window.webkitAudioContext)({ 
                sampleRate: 16000 
            });
            audioContextRef.current = context;

            // Load the worklet from the public folder
            await context.audioWorklet.addModule('/audio-processor.js');

            const source = context.createMediaStreamSource(stream);
            const captureNode = new AudioWorkletNode(context, 'audio-capture-processor');

            captureNode.port.onmessage = (event) => {
                bufferRef.current.push(...event.data);
                
                // 3 seconds of 16kHz audio = 48000 samples
                if (bufferRef.current.length >= 48000) {
                    sendChunk(new Float32Array(bufferRef.current));
                    bufferRef.current = [];
                }
            };

            // Connect nodes (Muting the output so user doesn't hear themselves)
            const gainNode = context.createGain();
            gainNode.gain.value = 0;
            source.connect(captureNode);
            captureNode.connect(gainNode);
            gainNode.connect(context.destination);

            setIsRecording(true);
        } catch (error) {
            console.error('Failed to start audio:', error);
        }
    }, [socket, room, userName]);

    const stopRecording = useCallback(() => {
        if (streamRef.current) {
            streamRef.current.getTracks().forEach(track => track.stop());
        }
        if (audioContextRef.current) {
            audioContextRef.current.close();
        }
        setIsRecording(false);
        bufferRef.current = [];
    }, []);

    const sendChunk = (float32Array) => {
        const int16Array = new Int16Array(float32Array.length);
        for (let i = 0; i < float32Array.length; i++) {
            let s = Math.max(-1, Math.min(1, float32Array[i]));
            int16Array[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
        }

        if (socket && socket.connected) {
            socket.emit("audio_chunk", {
                room: room,
                user_name: userName,
                audio: int16Array.buffer
            });
        }
    };

    return { isRecording, startRecording, stopRecording };
};
```

### Usage in a Component
```javascript
import React from 'react';
import { useAudioCapture } from './hooks/useAudioCapture';

const MeetingRoom = ({ socket, roomId, userName }) => {
    const { isRecording, startRecording, stopRecording } = useAudioCapture(socket, roomId, userName);

    return (
        <div>
            <h2>Room: {roomId}</h2>
            {isRecording ? (
                <button onClick={stopRecording}>Stop Mic</button>
            ) : (
                <button onClick={startRecording}>Start Mic</button>
            )}
        </div>
    );
};
```

## Summary
By using `AudioWorklets` and downsampling the audio to `16kHz Int16` natively in the browser, we shift the heavy processing load to the client's machine. This makes the backend `meeting-service` highly scalable, as it only needs to pass binary blobs directly into Redis without doing any math or formatting!