import express from 'express';
const app = express();
app.get('/', (_req, res) => res.json({ msg: 'DevOps Journey Day1 - Node', framework: 'express' }));
const port = 3000;
app.listen(port, () => console.log(`Node demo listening on :${port}`));
