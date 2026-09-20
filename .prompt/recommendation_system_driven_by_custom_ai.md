
based on the skills.md, can you provide a recommendation algorithm does the following?

(0) by querying what is available in the models, user can pick or upload their own recommendation system config.json? and user can pick their own config through a list given by the query. And user can toggle any time to disable the recommendation system settings by toggling.

(1) provides tool calling with (search_by_url, search_by_key_words)
    search_by_url: given a url, it will return the data regarding the video (including titles etc.)
    search_by_key_words: given a series of key words, it will return by round robin fashion from rumbles or youtube videos having these key words.

(2) recall watch histories (watch histories are from the backend databases) and ask the agent via tool calling what are some key words from these videos and then search them and then give the list to llm and llm will pick the playlists. 

example prompts:
1 - Can you give me a list of key words associated with the following video titles? Make sure respond in json {"keywords": [...]}

2 - Can you give me a list of youtube videos similar to the following one? youtube.com/watch?v=dQw4w9WgXcQ
youtube.com/watch?v=jNQXAC9IVRw. Your output has to following the following json format {"youtube": ["youtube.com/watch?v=dQw4w9WgXcQ" ...]}?


(3) support for when there's no videos loading (for tiktok feed view), then we will call the llm to get the list of videos via step (2) support for when loading a video via player, then it should should a list of videos to play next.

