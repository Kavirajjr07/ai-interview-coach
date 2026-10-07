/* InterviewAI CodeMirror Controller for Coding Interviews */

class CodingEditorManager {
    constructor() {
        this.editor = null;
        this.templates = {
            python: `def two_sum(nums, target):
    # Write your optimized solution here
    seen = {}
    for i, num in enumerate(nums):
        diff = target - num
        if diff in seen:
            return [seen[diff], i]
        seen[num] = i
    return []

# Test your code
print(two_sum([2, 7, 11, 15], 9))
`,
            java: `import java.util.HashMap;
import java.util.Map;

public class Solution {
    public int[] twoSum(int[] nums, int target) {
        // Write your optimized solution here
        Map<Integer, Integer> map = new HashMap<>();
        for (int i = 0; i < nums.length; i++) {
            int complement = target - nums[i];
            if (map.containsKey(complement)) {
                return new int[] { map.get(complement), i };
            }
            map.put(nums[i], i);
        }
        return new int[] {};
    }
}
`,
            cpp: `#include <iostream>
#include <vector>
#include <unordered_map>

using namespace std;

class Solution {
public:
    vector<int> twoSum(vector<int>& nums, int target) {
        // Write your optimized solution here
        unordered_map<int, int> seen;
        for (int i = 0; i < nums.size(); ++i) {
            int complement = target - nums[i];
            if (seen.find(complement) != seen.end()) {
                return {seen[complement], i};
            }
            seen[nums[i]] = i;
        }
        return {};
    }
};
`,
            javascript: `function twoSum(nums, target) {
    // Write your optimized solution here
    const map = new Map();
    for (let i = 0; i < nums.length; i++) {
        const complement = target - nums[i];
        if (map.has(complement)) {
            return [map.get(complement), i];
        }
        map.set(nums[i], i);
    }
    return [];
}

// Test your code
console.log(twoSum([2, 7, 11, 15], 9));
`
        };
        this.modeMappings = {
            python: 'python',
            java: 'text/x-java',
            cpp: 'text/x-c++src',
            javascript: 'javascript'
        };
    }

    init(elementId, initialLanguage = 'python') {
        const textarea = document.getElementById(elementId);
        if (!textarea) return;

        // Check if CodeMirror is loaded globally
        if (typeof CodeMirror === 'undefined') {
            console.error('CodeMirror CDN is missing. Fallback to basic textarea.');
            return;
        }

        this.editor = CodeMirror.fromTextArea(textarea, {
            lineNumbers: true,
            theme: 'dracula',
            mode: this.modeMappings[initialLanguage],
            indentUnit: 4,
            matchBrackets: true,
            autoCloseBrackets: true,
            lineWrapping: true
        });

        // Set default boilerplate
        this.setLanguage(initialLanguage);

        // Sync contents to textarea when changed
        this.editor.on('change', (instance) => {
            textarea.value = instance.getValue();
        });
    }

    setLanguage(lang) {
        if (!this.editor) return;
        const normalizedLang = lang.toLowerCase();
        
        // Update CodeMirror Mode
        if (this.modeMappings[normalizedLang]) {
            this.editor.setOption('mode', this.modeMappings[normalizedLang]);
        }

        // Apply Boilerplate Template
        if (this.templates[normalizedLang]) {
            this.editor.setValue(this.templates[normalizedLang]);
        }
    }

    getCode() {
        return this.editor ? this.editor.getValue() : '';
    }

    setCode(code) {
        if (this.editor) {
            this.editor.setValue(code);
        }
    }
}

// Global initialization helper
window.codeEditorManager = new CodingEditorManager();
