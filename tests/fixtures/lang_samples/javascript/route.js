const express = require("express");
const router = express.Router();

// POST /x with middleware chain
router.post("/x",
  logRequest,
  validateRequest,
  async (req, res) => {
    const id = req.body.id;
    if (!id) {
      return res.status(400).json({ error: "missing id" });
    }
    const result = await doWork(id);
    if (result.ok) {
      res.json({ data: result.data });
    } else {
      res.status(500).json({ error: result.message });
    }
  }
);

function logRequest(req, res, next) {
  console.log(req.method, req.path);
  next();
}

function validateRequest(req, res, next) {
  if (!req.body) {
    return res.status(400).json({ error: "no body" });
  }
  next();
}
