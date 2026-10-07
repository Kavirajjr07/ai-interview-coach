/* InterviewAI Voice Interaction & Speech-to-Text Manager */

class VoiceRecognitionManager {
    constructor() {
        this.recognition = null;
        this.isRecording = false;
        this.recordedText = '';
        this.confidenceList = [];
        this.onTextChangeCallback = null;
        this.onStatusChangeCallback = null;
        
        this.initRecognition();
    }

    initRecognition() {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SpeechRecognition) {
            console.warn('Web Speech API is not supported in this browser.');
            return;
        }

        this.recognition = new SpeechRecognition();
        this.recognition.continuous = true;
        this.recognition.interimResults = true;
        this.recognition.lang = 'en-US';

        this.recognition.onstart = () => {
            this.isRecording = true;
            this.confidenceList = [];
            this.updateStatus('recording');
        };

        this.recognition.onerror = (event) => {
            console.error('Speech recognition error:', event.error);
            this.stop();
            if (event.error === 'not-allowed') {
                showToast('Microphone access denied. Please allow mic permissions.', 'error');
            } else {
                showToast('Speech recognition error: ' + event.error, 'error');
            }
        };

        this.recognition.onend = () => {
            this.isRecording = false;
            this.updateStatus('idle');
        };

        this.recognition.onresult = (event) => {
            let interimTranscript = '';
            let finalTranscript = '';

            for (let i = event.resultIndex; i < event.results.length; ++i) {
                const result = event.results[i];
                if (result.isFinal) {
                    finalTranscript += result[0].transcript + ' ';
                    if (result[0].confidence) {
                        this.confidenceList.push(result[0].confidence);
                    }
                } else {
                    interimTranscript += result[0].transcript;
                }
            }

            // Combine previous text with new segments
            const currentOutput = (this.recordedText + finalTranscript + interimTranscript).trim();
            if (this.onTextChangeCallback) {
                this.onTextChangeCallback(currentOutput, interimTranscript === '');
            }
        };
    }

    start(initialText = '') {
        if (!this.recognition) {
            showToast('Speech Recognition is not supported by your browser. Please type your answer.', 'warning');
            return;
        }
        if (this.isRecording) return;

        this.recordedText = initialText ? initialText + ' ' : '';
        try {
            this.recognition.start();
        } catch (e) {
            console.error(e);
        }
    }

    stop() {
        if (!this.recognition || !this.isRecording) return;
        try {
            this.recognition.stop();
        } catch (e) {
            console.error(e);
        }
    }

    toggle(initialText = '') {
        if (this.isRecording) {
            this.stop();
        } else {
            this.start(initialText);
        }
    }

    onTextChange(callback) {
        this.onTextChangeCallback = callback;
    }

    onStatusChange(callback) {
        this.onStatusChangeCallback = callback;
    }

    updateStatus(status) {
        if (this.onStatusChangeCallback) {
            this.onStatusChangeCallback(status);
        }
    }

    getAverageConfidence() {
        if (this.confidenceList.length === 0) return 0.85; // Default mockup
        const sum = this.confidenceList.reduce((a, b) => a + b, 0);
        return sum / this.confidenceList.length;
    }

    // Static analysis helper for client-side filler word tracking
    static analyzeSpeechClarity(text) {
        if (!text) return { fillerWordCount: 0, density: 0, quality: 'Good' };
        
        const fillerWords = ['um', 'uh', 'like', 'so', 'you know', 'actually', 'basically'];
        const words = text.toLowerCase().match(/\b\w+\b/g) || [];
        const totalWords = words.length;
        
        let fillerWordCount = 0;
        
        // Scan for filler phrases
        const textLower = text.toLowerCase();
        fillerWords.forEach(phrase => {
            const regex = new RegExp('\\b' + phrase + '\\b', 'g');
            const matches = textLower.match(regex);
            if (matches) {
                fillerWordCount += matches.length;
            }
        });

        const density = totalWords > 0 ? (fillerWordCount / totalWords) * 100 : 0;
        let quality = 'Excellent';
        if (density > 10) quality = 'Needs Improvement';
        else if (density > 5) quality = 'Moderate';

        return {
            fillerWordCount,
            density: Math.round(density * 10) / 10,
            quality,
            wordCount: totalWords
        };
    }
}

// Global voice instance hook
window.voiceManager = new VoiceRecognitionManager();
