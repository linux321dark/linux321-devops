package main

import (
	"net/http"

	"github.com/gin-gonic/gin"
)

func main() {
	r := gin.Default()
	r.GET("/", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"msg": "DevOps Journey Day1 - Go", "framework": "gin"})
	})
	r.Run(":8080")
}
